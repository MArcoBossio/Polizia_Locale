import asyncio
import heapq
from typing import List, Tuple, Optional

import aiohttp

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
)
from ..persistent_cache import SQLiteCache
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
        # reuse synchronous acceptance logic from scraper
        from . import _accept_email as _accept_email_impl

        _accept_email_impl(email, ctx, html, page_is_polizia, source_kind)

    async with aiohttp.ClientSession() as session:
        # 0) seed pages
        seed_urls: List[str] = []
        if site_hint:
            parsed_hint = urlparse(site_hint if site_hint.startswith(("http://", "https://")) else "https://" + site_hint)
            if parsed_hint.netloc == parsed.netloc and parsed_hint.path not in ("", "/"):
                seed_urls.append(parsed_hint.geturl())

        for url in seed_urls:
            if time.monotonic() > deadline or (pec_all or mail_all):
                break
            status_code, final_url, text = await _get_page_async(session, url, req_timeout, page_cache, persistent_cache)
            if status_code != 200:
                continue
            pages_visited.append(final_url)
            pairs = await asyncio.to_thread(_extract_emails_with_context, text)
            for e, ctx in pairs:
                await asyncio.to_thread(_accept_email_sync, e, ctx, text, _path_is_polizia(final_url), "page_html")

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
                pairs = await asyncio.to_thread(_extract_emails_with_context, text)
                for e, ctx in pairs:
                    await asyncio.to_thread(_accept_email_sync, e, ctx, text, _path_is_polizia(final_url) or _path_is_polizia(origin_url), "page_html")

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
                    pairs = await asyncio.to_thread(_extract_emails_with_context, text2)
                    for e, ctx in pairs:
                        await asyncio.to_thread(_accept_email_sync, e, ctx, text2, _path_is_polizia(final_url2) or _path_is_polizia(u), "page_html")

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
                pairs = await asyncio.to_thread(_extract_emails_with_context, text)
                for e, ctx in pairs:
                    await asyncio.to_thread(_accept_email_sync, e, ctx, text, True, "page_html")
                if not (pec_all or mail_all):
                    anchors = await asyncio.to_thread(lambda: list(__import__("itertools").islice(_candidate_links(text, final_url, True), 20)))
                    for href, _score in anchors:
                        u = urljoin(final_url, href)
                        if not u.startswith("http"):
                            continue
                        status_code2, final_url2, text2 = await _get_page_async(session, u, req_timeout, page_cache, persistent_cache)
                        if status_code2 != 200:
                            continue
                        pages_visited.append(final_url2)
                        pairs2 = await asyncio.to_thread(_extract_emails_with_context, text2)
                        for e, ctx in pairs2:
                            await asyncio.to_thread(_accept_email_sync, e, ctx, text2, True, "page_html")

        # 3) homepage + frontier BFS
        home_html = ""
        home_url = site
        if not (pec_all or mail_all):
            status_code, final_url, text = await _get_page_async(session, site, req_timeout, page_cache, persistent_cache)
            if status_code == 200:
                pages_visited.append(final_url)
                home_html = text
                home_url = final_url
                await asyncio.to_thread(lambda: _note("homepage"))

                frontier: list[tuple[int, str]] = []
                seen_frontier: set[str] = {final_url, site}
                await asyncio.to_thread(_enqueue_candidate_pages, frontier, text, base, False, seen_frontier, 1, max(16, max_candidates * 6))

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
                        pairs = await asyncio.to_thread(_extract_emails_with_context, text)
                        for e, ctx in pairs:
                            await asyncio.to_thread(_accept_email_sync, e, ctx, text, is_pl, "page_html")
                        new_soup_pages.append((final_url, text, is_pl))
                        if is_pl:
                            await asyncio.to_thread(_maybe_extract_pdfs, None, text, final_url, base, deadline, req_timeout, strict_pl_local, pdf_extract, _accept_email_sync, pec_all, mail_all)
                    for page_url, text, is_pl in new_soup_pages:
                        if time.monotonic() > deadline or (pec_all or mail_all):
                            break
                        await asyncio.to_thread(_enqueue_candidate_pages, frontier, text, base, is_pl, seen_frontier, 2 if is_pl else 1, 40)

        # 4) broad fallback
        if not (pec_all or mail_all):
            if not home_html:
                status_code, final_url, text = await _get_page_async(session, site, req_timeout, page_cache, persistent_cache)
                if status_code == 200:
                    home_html = text
                    home_url = final_url
            broad_candidates = await asyncio.to_thread(_broad_candidate_links, home_html or "", base, max(4, int(max_candidates) * 2), True)
            if broad_candidates or strong_pl_signal_seen:
                fetched_broad = await _fetch_many_async(session, broad_candidates, req_timeout)
                for origin_url, status_code, final_url, text in fetched_broad:
                    if time.monotonic() > deadline or (pec_all or mail_all):
                        break
                    if not status_code or not text:
                        continue
                    pages_visited.append(final_url)
                    pairs = await asyncio.to_thread(_extract_emails_with_context, text)
                    for e, ctx in pairs:
                        await asyncio.to_thread(_accept_email_sync, e, ctx, text, _path_is_polizia(final_url) or _path_is_polizia(origin_url), "page_html")
                    if _path_is_polizia(final_url) and pdf_extract and not (pec_all or mail_all):
                        await asyncio.to_thread(_maybe_extract_pdfs, None, text, final_url, base, deadline, req_timeout, strict_pl_local, pdf_extract, _accept_email_sync, pec_all, mail_all)

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
