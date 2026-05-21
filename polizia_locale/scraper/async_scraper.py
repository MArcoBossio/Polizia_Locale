import asyncio
import heapq
from typing import List, Tuple, Optional

import aiohttp
from concurrent.futures import ThreadPoolExecutor

from . import (
    _extract_emails_with_context,
    _candidate_links,
    _broad_candidate_links,
    _maybe_extract_pdfs,
    _should_try_browser_fallback,
    _browser_rendered_pairs,
    _browser_rendered_text,
    _ocr_page_screenshot,
    _path_is_polizia,
    _enqueue_candidate_pages,
    _DIRECT_PATH_HINTS,
    EMAIL_RE,
    ScrapeResult,
    find_comune_website,
    _score_email_context,
    _is_pec,
)
from ..persistent_cache import SQLiteCache
from ..utils import is_likely_personal_email
from urllib.parse import urlparse, urljoin
import time


async def _get_page_async(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int,
    page_cache: dict,
    persistent: Optional[SQLiteCache],
) -> tuple[int, str, str]:
    """Async-aware get: consult in-memory cache, persistent SQLite, then fetch via aiohttp."""
    if url in page_cache:
        return page_cache[url]
    if persistent is not None:
        try:
            row = await asyncio.to_thread(persistent.get, url)
            if row is not None:
                status, final_url, text, ts = row
                page_cache[url] = (status, final_url, text)
                return page_cache[url]
        except Exception:
            pass
    try:
        async with session.get(url, timeout=timeout) as resp:
            text = await resp.text()
            result = (resp.status, str(resp.url), text)
    except Exception:
        result = (0, url, "")
    page_cache[url] = result
    if persistent is not None:
        try:
            await asyncio.to_thread(persistent.set, url, result[0], result[1], result[2])
        except Exception:
            pass
    return result


# Shared threadpool for running synchronous helpers to avoid unbounded threads
_EXECUTOR = ThreadPoolExecutor(max_workers=6)


async def _run_in_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, func, *args)


async def _fetch_many_async(session: aiohttp.ClientSession, urls: List[str], timeout: int) -> List[Tuple[str, int, str, str]]:
    sem = asyncio.Semaphore(12)

    async def _fetch(u: str):
        async with sem:
            try:
                async with session.get(u, timeout=timeout) as resp:
                    text = await resp.text()
                    return (u, resp.status, str(resp.url), text)
            except Exception:
                return (u, 0, u, "")

    tasks = [asyncio.create_task(_fetch(u)) for u in urls]
    results: List[Tuple[str, int, str, str]] = []
    for t in asyncio.as_completed(tasks):
        results.append(await t)
    return results


async def async_scrape_polizia_locale(
    comune: str,
    provincia: str,
    codice_istat: str,
    site_hint: str = "",
    timeout: int = 15,
    total_budget: float = 40.0,
    max_candidates: int = 4,
    strict_pl_local: bool = True,
    pdf_extract: bool = True,
) -> ScrapeResult | None:
    deadline = time.monotonic() + total_budget
    req_timeout = max(1, int(timeout))
    site = site_hint.strip()
    persistent_cache = SQLiteCache()

    if not site:
        site = await asyncio.to_thread(find_comune_website, None, comune, provincia)
        if not site or time.monotonic() > deadline:
            return None

    parsed = urlparse(site)
    if not parsed.scheme:
        site = "https://" + site
        parsed = urlparse(site)
    base = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.lstrip("www.")

    page_cache: dict[str, tuple[int, str, str]] = {}
    pages_visited: list[str] = []
    pec_all: set[str] = set()
    mail_all: set[str] = set()
    matched_by: set[str] = set()
    strong_pl_signal_seen = False

    def _note(kind: str) -> None:
        matched_by.add(kind)

    def _accept_email_sync(email: str, ctx: str, html: str, page_is_polizia: bool, source_kind: str) -> None:
        ctx_score, reasons = _score_email_context(html, email, ctx)
        local = email.split("@")[0].lower()
        is_pl_local = any(
            k in local
            for k in (
                "polizialocale",
                "poliziamunicipale",
                "vigili",
                "comandopm",
                "comandopl",
                "pol.locale",
                "pol.municipale",
                "pm.",
                "pl.",
            )
        )
        is_pl_ctx = any(
            k in ctx.lower()
            for k in (
                "polizia locale",
                "polizia municipale",
                "polizia urbana",
                "vigili urbani",
                "comando",
                "corpo di polizia",
                "corpo unico",
                "servizio associato di polizia",
                "servizio associato di vigilanza",
                "ufficio vigilanza",
                "police locale",
                "service de police",
                "service de police locale",
                "union des communes",
                "municipalite",
                "municipalità",
                "vigiles",
                "ortspolizei",
                "gemeindepolizei",
                "polizeiamt",
            )
        )
        generic_local_parts = {
            "info",
            "segreteria",
            "protocollo",
            "ufficio",
            "amministrazione",
            "contatti",
            "contact",
            "help",
            "service",
            "webmaster",
            "noreply",
            "postmaster",
        }
        if strict_pl_local:
            if not is_pl_local and local in generic_local_parts:
                return
            if not (is_pl_local or page_is_polizia or is_pl_ctx or ctx_score >= 4):
                return
            if is_likely_personal_email(email) and not (is_pl_local or page_is_polizia or ctx_score >= 5):
                return
        else:
            if not (page_is_polizia or is_pl_local or is_pl_ctx or ctx_score >= 2):
                return
            if is_likely_personal_email(email) and not (is_pl_local or page_is_polizia or ctx_score >= 4):
                return

        if local in {"noreply", "no-reply", "webmaster", "postmaster"}:
            return
        if _is_pec(email, ctx):
            pec_all.add(email)
        else:
            mail_all.add(email)
        for reason in reasons:
            _note(reason)
        _note(source_kind)

    def _note_strong_signal(html: str, page_url: str, page_is_polizia: bool) -> None:
        nonlocal strong_pl_signal_seen
        if page_is_polizia or _path_is_polizia(page_url):
            strong_pl_signal_seen = True
            return
        normalized = html.lower()
        if any(
            k in normalized
            for k in (
                "polizia locale",
                "polizia municipale",
                "vigili urbani",
                "comando",
                "corpo di polizia",
                "servizio associato di polizia",
            )
        ):
            strong_pl_signal_seen = True

    def _harvest(html: str, url_is_pl: bool, page_url: str = "") -> None:
        found_before = bool(pec_all or mail_all)
        _note_strong_signal(html, page_url, url_is_pl)
        pairs = _extract_emails_with_context(html)
        for e, ctx in pairs:
            _accept_email_sync(e, ctx, html, url_is_pl, "page_html")

        if (not found_before) and not (pec_all or mail_all) and page_url:
            if _should_try_browser_fallback(html, page_url, site_root=site, url_is_pl=url_is_pl):
                rendered_pairs = _browser_rendered_pairs(page_url)
                for e, ctx in rendered_pairs:
                    _accept_email_sync(e, ctx, ctx, url_is_pl, "js_render")

                if not rendered_pairs:
                    rendered_text = _browser_rendered_text(page_url)
                    if rendered_text:
                        for m in EMAIL_RE.finditer(rendered_text):
                            e = m.group(0)
                            ctx = rendered_text[max(0, m.start() - 80):m.end() + 80]
                            _accept_email_sync(e, ctx, rendered_text, url_is_pl, "js_render")

                if not (pec_all or mail_all):
                    ocr_text = _ocr_page_screenshot(page_url)
                    if ocr_text:
                        for m in EMAIL_RE.finditer(ocr_text):
                            e = m.group(0)
                            ctx = ocr_text[max(0, m.start() - 80):m.end() + 80]
                            _accept_email_sync(e, ctx, ocr_text, url_is_pl, "ocr")

    async with aiohttp.ClientSession() as session:
        # 0) seed pages
        seed_urls: List[str] = []
        if site_hint:
            parsed_hint = urlparse(site_hint if site_hint.startswith(("http://", "https://")) else "https://" + site_hint)
            if parsed_hint.netloc == parsed.netloc and parsed_hint.path not in ("", "/"):
                seed_urls.append(parsed_hint.geturl())

        home_html = ""
        home_url = site

        for url in seed_urls:
            if time.monotonic() > deadline or (pec_all or mail_all):
                break
            status_code, final_url, text = await _get_page_async(session, url, req_timeout, page_cache, persistent_cache)
            if status_code != 200:
                continue
            pages_visited.append(final_url)
            pairs = await _run_in_executor(_extract_emails_with_context, text)
            for e, ctx in pairs:
                await _run_in_executor(_accept_email_sync, e, ctx, text, _path_is_polizia(final_url), "page_html")

        if not (pec_all or mail_all):
            status_code, final_url, text = await _get_page_async(session, site, req_timeout, page_cache, persistent_cache)
            if status_code == 200:
                pages_visited.append(final_url)
                home_html = text
                home_url = final_url
                _harvest(text, url_is_pl=False, page_url=final_url)

        # 1) direct paths
        if not (pec_all or mail_all):
            direct_urls = [base + p for p in _DIRECT_PATH_HINTS[: max(6, max_candidates * 2)]]
            fetched_direct = await _fetch_many_async(session, direct_urls, req_timeout)
            for origin_url, status_code, final_url, text in fetched_direct:
                if time.monotonic() > deadline or (pec_all or mail_all):
                    break
                if not status_code or not text:
                    continue
                pages_visited.append(final_url)
                pairs = await _run_in_executor(_extract_emails_with_context, text)
                for e, ctx in pairs:
                    await _run_in_executor(_accept_email_sync, e, ctx, text, _path_is_polizia(final_url) or _path_is_polizia(origin_url), "page_html")

        # 1b) sitemaps
        if not (pec_all or mail_all):
            sitemap_seed_urls = [urljoin(base, p) for p in ("/robots.txt", "/sitemap.xml", "/sitemap_index.xml")]
            sitemap_urls: list[str] = []
            for seed_url in sitemap_seed_urls:
                if time.monotonic() > deadline or len(sitemap_urls) >= 8:
                    break
                status_code, _final, text = await _get_page_async(session, seed_url, req_timeout, page_cache, persistent_cache)
                if status_code != 200:
                    continue
                if seed_url.endswith("robots.txt"):
                    for line in text.splitlines():
                        if line.lower().startswith("sitemap:"):
                            sitemap_urls.append(line.split(":", 1)[1].strip())
                else:
                    for loc in __import__("re").findall(r"<loc>(.*?)</loc>", text, flags=__import__("re").IGNORECASE | __import__("re").DOTALL):
                        if loc.endswith((".xml", ".xml.gz")):
                            sitemap_urls.append(loc.strip())

            seen_sitemaps: set[str] = set()
            for sitemap_url in sitemap_urls:
                if time.monotonic() > deadline or (pec_all or mail_all):
                    break
                if sitemap_url in seen_sitemaps:
                    continue
                seen_sitemaps.add(sitemap_url)
                status_code, final_url, text = await _get_page_async(session, sitemap_url, req_timeout, page_cache, persistent_cache)
                if status_code != 200:
                    continue
                if not any(k in text.lower() for k in ("polizia", "vigili", "municipale", "comando", "sicurezza urbana")):
                    continue
                for loc in __import__("re").findall(r"<loc>(.*?)</loc>", text, flags=__import__("re").IGNORECASE | __import__("re").DOTALL):
                    u = loc.strip()
                    if not u.startswith(("http://", "https://")):
                        continue
                    low_u = u.lower()
                    if not any(k in low_u for k in ("polizia", "vigili", "municipale", "comando", "sicurezza-urbana", "sicurezza urbana", "punto_contatto")):
                        continue
                    if time.monotonic() > deadline or (pec_all or mail_all):
                        break
                    status_code2, final_url2, text2 = await _get_page_async(session, u, req_timeout, page_cache, persistent_cache)
                    if status_code2 != 200:
                        continue
                    pages_visited.append(final_url2)
                    pairs = await _run_in_executor(_extract_emails_with_context, text2)
                    for e, ctx in pairs:
                        await _run_in_executor(_accept_email_sync, e, ctx, text2, _path_is_polizia(final_url2) or _path_is_polizia(u), "page_html")

        # 2) subdomains
        if not (pec_all or mail_all):
            for sub in ("pm", "pl", "polizia"):
                if time.monotonic() > deadline or (pec_all or mail_all):
                    break
                sub_url = f"{parsed.scheme}://{sub}.{host}/"
                status_code, final_url, text = await _get_page_async(session, sub_url, req_timeout, page_cache, persistent_cache)
                if status_code != 200:
                    continue
                pages_visited.append(final_url)
                pairs = await _run_in_executor(_extract_emails_with_context, text)
                for e, ctx in pairs:
                    await _run_in_executor(_accept_email_sync, e, ctx, text, True, "page_html")
                if not (pec_all or mail_all):
                    anchors = await _run_in_executor(lambda: list(__import__("itertools").islice(_candidate_links(text, final_url, True), 20)))
                    for href, _score in anchors:
                        u = urljoin(final_url, href)
                        if not u.startswith("http"):
                            continue
                        status_code2, final_url2, text2 = await _get_page_async(session, u, req_timeout, page_cache, persistent_cache)
                        if status_code2 != 200:
                            continue
                        pages_visited.append(final_url2)
                        pairs2 = await _run_in_executor(_extract_emails_with_context, text2)
                        for e, ctx in pairs2:
                            await _run_in_executor(_accept_email_sync, e, ctx, text2, True, "page_html")

        # 3) homepage + frontier BFS
        if not home_html and not (pec_all or mail_all):
            status_code, final_url, text = await _get_page_async(session, site, req_timeout, page_cache, persistent_cache)
            if status_code == 200:
                pages_visited.append(final_url)
                home_html = text
                home_url = final_url
                await _run_in_executor(lambda: _note("homepage"))

                frontier: list[tuple[int, str]] = []
                seen_frontier: set[str] = {final_url, site}
                await _run_in_executor(_enqueue_candidate_pages, frontier, text, base, False, seen_frontier, 1, max(16, max_candidates * 6))

                fetched_pages = 0
                while frontier and fetched_pages < 28 and time.monotonic() <= deadline and not (pec_all or mail_all):
                    # pop top N
                    n = min(6, len(frontier))
                    batch = []
                    for _ in range(n):
                        try:
                            score_neg, url = heapq.heappop(frontier)
                        except IndexError:
                            break
                        batch.append(url)
                    fetched = await _fetch_many_async(session, batch, req_timeout)
                    new_soup_pages: list[tuple[str, str, bool]] = []
                    for origin_url, status_code, final_url, text in fetched:
                        if time.monotonic() > deadline or (pec_all or mail_all):
                            break
                        if not status_code or not text:
                            continue
                        fetched_pages += 1
                        pages_visited.append(final_url)
                        is_pl = _path_is_polizia(final_url) or _path_is_polizia(origin_url)
                        pairs = await _run_in_executor(_extract_emails_with_context, text)
                        for e, ctx in pairs:
                            await _run_in_executor(_accept_email_sync, e, ctx, text, is_pl, "page_html")
                        new_soup_pages.append((final_url, text, is_pl))
                        if is_pl:
                            await _run_in_executor(_maybe_extract_pdfs, None, text, final_url, base, deadline, req_timeout, strict_pl_local, pdf_extract, _accept_email_sync, pec_all, mail_all)
                    for page_url, text, is_pl in new_soup_pages:
                        if time.monotonic() > deadline or (pec_all or mail_all):
                            break
                        await _run_in_executor(_enqueue_candidate_pages, frontier, text, base, is_pl, seen_frontier, 2 if is_pl else 1, 40)

        # 4) broad fallback
        if not (pec_all or mail_all):
            if not home_html:
                status_code, final_url, text = await _get_page_async(session, site, req_timeout, page_cache, persistent_cache)
                if status_code == 200:
                    home_html = text
                    home_url = final_url
            broad_candidates = await _run_in_executor(_broad_candidate_links, home_html or "", base, max(4, int(max_candidates) * 2), True)
            if broad_candidates or strong_pl_signal_seen:
                fetched_broad = await _fetch_many_async(session, broad_candidates, req_timeout)
                for origin_url, status_code, final_url, text in fetched_broad:
                    if time.monotonic() > deadline or (pec_all or mail_all):
                        break
                    if not status_code or not text:
                        continue
                    pages_visited.append(final_url)
                    pairs = await _run_in_executor(_extract_emails_with_context, text)
                    for e, ctx in pairs:
                        await _run_in_executor(_accept_email_sync, e, ctx, text, _path_is_polizia(final_url) or _path_is_polizia(origin_url), "page_html")
                    if _path_is_polizia(final_url) and pdf_extract and not (pec_all or mail_all):
                        await _run_in_executor(_maybe_extract_pdfs, None, text, final_url, base, deadline, req_timeout, strict_pl_local, pdf_extract, _accept_email_sync, pec_all, mail_all)

    if not pec_all and not mail_all:
        return None

    confidence = 0.45
    if matched_by:
        confidence += min(0.35, 0.05 * len(matched_by))
    if any(_path_is_polizia(p) for p in pages_visited):
        confidence += 0.1

    return ScrapeResult(
        comune=comune,
        codice_istat=codice_istat,
        pec=" | ".join(sorted(pec_all)),
        email=" | ".join(sorted(mail_all)),
        sito=base,
        pagina=pages_visited[-1] if pages_visited else "",
        confidence=min(confidence, 0.99),
        matched_by=" | ".join(sorted(matched_by)),
    )
