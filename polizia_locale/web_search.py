"""Ricerca web di mail PL-specifiche tramite browser headless (Playwright + Bing).

Quando lo scraping diretto del sito istituzionale del Comune non trova mail
PL-specifiche, questo modulo replica una ricerca web "manuale" usando un
browser reale (Chromium headless via Playwright) su Bing. Estrae le mail
PL-specifiche presenti negli snippet dei risultati.

Bing è scelto perché ha policy di scraping più tolleranti rispetto a Google
e ritorna risultati anche senza login. Playwright è necessario perché Bing
con un semplice requests.get restituisce HTML privo di risultati.

Uso tipico (un browser per tutta la sessione):

    finder = WebSearchFinder()
    finder.start()
    try:
        mails_pec = finder.search_polizia_locale("Prato", "Prato")
    finally:
        finder.stop()
"""
from __future__ import annotations

import asyncio
import re
import threading
from contextlib import suppress

from .indicepa import is_pl_specific_email
from .scraper import EMAIL_RE, _is_pec


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)


def _build_queries(comune: str, provincia: str) -> list[str]:
    """Ordine dalla più specifica alla più larga (si ferma alla prima utile).

    Include la provincia per disambiguare comuni con stesso nome (es. "Massa"
    vs "Massa e Cozzile", "Castro" vs "Castrovillari").
    """
    base = comune.strip()
    prov = provincia.strip()
    q_prov = f"({prov}) " if prov else ""
    return [
        f'"polizia locale" {base} {q_prov}mail',
        f'"polizia municipale" {base} {q_prov}email',
        f'polizia locale {base} {q_prov}contatti email',
        f'polizia municipale {base} {q_prov}mail comando',
        f'vigili urbani {base} {q_prov}mail',
        f'"polizia locale" "{base}" mail',
        f'"polizia municipale" "{base}" email',
    ]


class WebSearchFinder:
    """Gestisce un singolo browser headless riusato per tutte le query.

    L'oggetto vive su un thread separato con un proprio asyncio loop, così che
    possa essere usato da codice sincrono e thread pool senza incastrarsi con
    l'event loop principale.
    """

    def __init__(self, timeout_ms: int = 15000):
        self.timeout_ms = timeout_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._playwright = None
        self._browser = None
        self._ready = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _init():
            from playwright.async_api import async_playwright
            try:
                print("[DEBUG] Avvio Playwright...")
                self._playwright = await async_playwright().start()
                print("[DEBUG] Lancio Chromium...")
                self._browser = await self._playwright.chromium.launch(headless=True)
                print("[DEBUG] Chromium avviato OK")
                self._ready.set()
            except Exception as e:
                print(f"[ERROR] Fallita inizializzazione browser: {e}")
                self._ready.set()

        self._loop.run_until_complete(_init())
        self._loop.run_forever()

    def stop(self) -> None:
        if self._loop is None:
            return

        async def _close():
            with suppress(Exception):
                if self._browser:
                    await self._browser.close()
            with suppress(Exception):
                if self._playwright:
                    await self._playwright.stop()

        future = asyncio.run_coroutine_threadsafe(_close(), self._loop)
        with suppress(Exception):
            future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread = None

    # ------------------------------------------------------------------- queries
    async def _fetch(self, query: str) -> str:
        ctx = await self._browser.new_context(user_agent=_USER_AGENT, locale="it-IT")
        page = await ctx.new_page()
        try:
            url = "https://www.bing.com/search?q=" + query.replace(" ", "+") + "&setlang=it"
            await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            await page.wait_for_timeout(700)
            return await page.content()
        except Exception:
            return ""
        finally:
            with suppress(Exception):
                await ctx.close()

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    # ------------------------------------------------------------------- pubblica
    def search_polizia_locale(
        self,
        comune: str,
        provincia: str = "",
        domain_hint: str = "",
    ) -> tuple[set[str], set[str]]:
        """Ritorna (pec_set, mail_set) di mail PL-specifiche trovate via Bing.

        Se `domain_hint` è fornito (es. 'comune.massa.ms.it'), filtra solo le
        mail il cui dominio è coerente con il sito del comune. Questo evita
        false attribuzioni a comuni con nome simile (es. cercando "Massa" e
        trovando una mail di "Massa e Cozzile").
        """
        pec: set[str] = set()
        mail: set[str] = set()
        # estrai il dominio "core" del comune (es. comune.massa.ms.it)
        host = domain_hint.lower().lstrip("www.").strip("/") if domain_hint else ""

        def _domain_ok(email: str) -> bool:
            if not host:
                return True
            domain = email.split("@", 1)[1].lower() if "@" in email else ""
            # accetta dominio identico o sottodominio (es. pec.comune.massa.ms.it)
            return domain == host or domain.endswith("." + host) or host.endswith("." + domain)

        for query in _build_queries(comune, provincia):
            try:
                html = self._run(self._fetch(query))
            except Exception:
                continue
            if not html:
                continue
            for m in EMAIL_RE.finditer(html):
                email = m.group(0)
                if not is_pl_specific_email(email):
                    continue
                if not _domain_ok(email):
                    continue
                start = max(0, m.start() - 80)
                end = min(len(html), m.end() + 80)
                ctx = html[start:end]
                if _is_pec(email, ctx):
                    pec.add(email)
                else:
                    mail.add(email)
            if pec or mail:
                # primo successo: si ferma
                break
        return pec, mail
