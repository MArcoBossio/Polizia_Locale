"""CLI principale per la ricerca delle PEC/Email della Polizia Locale."""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from .comuni import load_comuni
from .exporter import export_all
from .indicepa import find_polizia_locale_aoo, find_polizia_locale_uo, load_enti_index
from .regions import list_regions, resolve_region
from .scraper import scrape_polizia_locale
from .unioni import (
    fetch_member_comuni,
    find_unioni_with_polizia_locale,
    match_member_comuni,
)


def _load_env() -> None:
    """Carica /app/.env se presente (le chiavi API arrivano da lì)."""
    for path in (".env", "/app/.env", os.path.join(os.path.dirname(__file__), "..", ".env")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_load_env()


BANNER = r"""
============================================================
  POLIZIA LOCALE / MUNICIPALE - PEC & MAIL FINDER
  Fonti: ISTAT (comuni) + IndicePA (PEC ufficiali) + scraping
============================================================
"""


def _interactive_pick_region() -> tuple[str, str]:
    regions = list_regions()
    print("\nSeleziona una regione italiana:\n")
    for i, (code, name) in enumerate(regions, start=1):
        print(f"  {i:2d}. [{code}] {name}")
    while True:
        raw = input("\nNumero, codice o nome regione: ").strip()
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(regions):
            return regions[int(raw) - 1]
        match = resolve_region(raw)
        if match:
            return match
        print("Regione non riconosciuta, riprova.")


def _run(region_code: str, region_name: str, args) -> int:
    print(BANNER)
    print(f"Regione selezionata: [{region_code}] {region_name}\n")

    print("[1/4] Scarico l'elenco dei comuni dall'ISTAT...")
    comuni = load_comuni(region_code)
    print(f"      {len(comuni)} comuni caricati.")

    print("[2/4] Scarico i dataset IndicePA (UO + AOO)...")
    istat_codes = [c.codice_istat for c in comuni]
    found_uo = find_polizia_locale_uo(istat_codes, strict=args.strict)
    found_aoo = find_polizia_locale_aoo(istat_codes, strict=args.strict)
    print(
        f"      {len(found_uo)} UO + {len(found_aoo)} AOO 'Polizia Locale/Municipale' "
        f"trovate su IndicePA (strict={args.strict})."
    )

    # indicizza per codice ISTAT — UO ha priorità, AOO aggiunto se nuovo comune
    by_istat: dict[str, list] = {}
    for rec in found_uo:
        by_istat.setdefault(rec.codice_istat, []).append(rec)
    for rec in found_aoo:
        if rec.codice_istat not in by_istat:
            by_istat[rec.codice_istat] = [rec]

    # --- Espansione delle Unioni di Comuni / Consorzi PL ---
    union_records_by_istat: dict[str, dict] = {}
    if args.expand_unioni:
        try:
            all_unioni = find_unioni_with_polizia_locale(region_code=region_code)
        except Exception as e:
            print(f"      Avviso: impossibile caricare le Unioni ({e})")
            all_unioni = []
        if all_unioni:
            def _process(u):
                try:
                    dedicated, homepage = fetch_member_comuni(u.sito, timeout=args.timeout)
                except Exception:
                    return u, []
                matched = match_member_comuni(dedicated, comuni)
                # NIENTE fallback su homepage: troppo rumore (es. menzioni
                # casuali di "Milano" su sito di una piccola Unione)
                return u, matched

            print(
                f"      Espansione Unioni/Consorzi PL: analizzo {len(all_unioni)} enti "
                f"intercomunali della regione (workers={args.workers})..."
            )
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(_process, u) for u in all_unioni]
                pairs = []
                for fut in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc="Unioni",
                    unit="ente",
                ):
                    pairs.append(fut.result())
            unioni_in_region = [(u, m) for (u, m) in pairs if m]
            # mappa codice ISTAT -> sigla provincia (per filtro membri Unione)
            sigla_by_istat = {c.codice_istat: c.sigla_provincia for c in comuni}
            total_attrib = 0
            for u, matched in unioni_in_region:
                # provincia della sede dell'Unione: i membri devono appartenere
                # alla stessa provincia (le Unioni sono per legge sub-provinciali).
                # Inoltre escludiamo i capoluoghi di provincia: NON sono mai
                # parte di Unioni (raro, e fonte di falsi positivi).
                sigla_unione = sigla_by_istat.get(u.codice_comune_istat_sede, "")
                # set dei capoluoghi (codice istat finiscono in '001' per i 6-char,
                # ma è euristico; meglio: comune con nome uguale alla provincia)
                for c in matched:
                    if c.codice_istat in by_istat:
                        continue  # ha già una PL diretta
                    if sigla_unione and c.sigla_provincia != sigla_unione:
                        continue
                    # esclusione capoluoghi di provincia (mai membri di Unioni)
                    if c.nome.strip().lower() == c.provincia.strip().lower():
                        continue
                    union_records_by_istat.setdefault(c.codice_istat, {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": u.denominazione_ente,
                        "codice_ipa": u.codice_ipa,
                        "descrizione_uo": f"{u.descrizione_uo} (gestione associata)",
                        "pec": u.pec,
                        "email": u.email,
                        "telefono": u.telefono,
                        "indirizzo": u.indirizzo,
                        "cap": u.cap,
                        "sito": u.sito,
                        "fonte": "IndicePA-Unione",
                    })
                    total_attrib += 1
            print(
                f"      Unioni che servono comuni della regione: {len(unioni_in_region)} "
                f"→ {total_attrib} comuni associati (filtrati per provincia)."
            )

    enti_idx = None
    if args.scrape or args.include_comune_pec:
        print("[3/4] Carico l'indice Enti per recuperare i siti istituzionali...")
        try:
            enti_idx = load_enti_index()
        except Exception as e:
            print(f"      Avviso: impossibile caricare il dataset Enti ({e})")
            enti_idx = {}

    # Indice PEC istituzionale del Comune (per Codice_comune_ISTAT) per arricchimento
    pec_comune_by_istat: dict[str, dict] = {}
    if enti_idx:
        for info in enti_idx.values():
            ci = info.get("codice_istat")
            if ci:
                pec_comune_by_istat.setdefault(ci, info)

    rows: list[dict] = []
    missing: list = []

    def _enrich_with_comune_pec(d: dict, c) -> dict:
        """Se il record IndicePA non ha PEC, aggiunge la PEC istituzionale del Comune
        e la marca come fonte 'IndicePA+PEC Comune'."""
        if d.get("pec"):
            return d
        if not args.include_comune_pec:
            return d
        info = pec_comune_by_istat.get(c.codice_istat)
        if info and info.get("pec_comune"):
            d["pec"] = info["pec_comune"]
            # se non c'era email originaria, copia anche quella generica
            if not d.get("email") and info.get("mail_comune"):
                d["email"] = info["mail_comune"]
            d["fonte"] = d.get("fonte", "IndicePA") + " + PEC Comune"
            if not d.get("sito"):
                d["sito"] = info.get("sito", "")
        return d

    for c in comuni:
        matches = by_istat.get(c.codice_istat, [])
        if matches:
            for rec in matches:
                d = rec.as_dict()
                d.update(
                    {
                        "comune": c.nome,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                    }
                )
                d = _enrich_with_comune_pec(d, c)
                rows.append(d)
        elif c.codice_istat in union_records_by_istat:
            rec = union_records_by_istat[c.codice_istat]
            rec = _enrich_with_comune_pec(rec, c)
            rows.append(rec)
        else:
            missing.append(c)

    print(
        f"      Comuni con match IndicePA: {len(comuni) - len(missing)} / {len(comuni)}"
    )

    if args.scrape and missing:
        # eventuale limite di scraping (utile per validazione su regioni grandi)
        to_scrape = missing
        skipped: list = []
        if args.scrape_limit and args.scrape_limit > 0 and len(missing) > args.scrape_limit:
            to_scrape = missing[: args.scrape_limit]
            skipped = missing[args.scrape_limit :]
            print(
                f"[4/4] Fallback scraping limitato ai primi {len(to_scrape)} comuni "
                f"(altri {len(skipped)} segnati NON TROVATO)."
            )
        else:
            print(
                f"[4/4] Fallback scraping per {len(to_scrape)} comuni senza PEC ufficiale dedicata "
                f"(workers={args.workers})..."
            )

        site_by_istat: dict[str, str] = {}
        pec_comune_by_istat: dict[str, dict] = {}
        if enti_idx:
            for info in enti_idx.values():
                ci = info.get("codice_istat")
                if not ci:
                    continue
                if info.get("sito"):
                    site_by_istat.setdefault(ci, info["sito"])
                pec_comune_by_istat.setdefault(ci, info)

        def _scrape_one(c):
            site_hint = site_by_istat.get(c.codice_istat, "")
            try:
                return c, scrape_polizia_locale(
                    c.nome,
                    c.provincia,
                    c.codice_istat,
                    site_hint=site_hint,
                    timeout=args.timeout,
                    strict_pl_local=args.strict,
                    pdf_extract=args.pdf_extract,
                )
            except Exception:
                return c, None

        results: list = []
        if args.workers > 1:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(_scrape_one, c) for c in to_scrape]
                for fut in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc="Scraping",
                    unit="comune",
                ):
                    results.append(fut.result())
        else:
            for c in to_scrape:
                results.append(_scrape_one(c))
                time.sleep(args.sleep)

        # Step 4b: verifica dei risultati dello scraping sul sito ufficiale
        def _verify_scraped_result(c, pec_set: set[str], mail_set: set[str]) -> tuple[set[str], set[str]]:
            """Verifica che le email trovate nello scraping siano presenti sul sito."""
            if not (pec_set or mail_set):
                return set(), set()
            if not args.reliability_check:
                return pec_set, mail_set

            site = site_by_istat.get(c.codice_istat, "")
            if not site:
                # senza sito ufficiale verifichiamo almeno che l'email sia nel dominio del sito
                return pec_set, mail_set

            try:
                from .reliability import verify_emails_on_site
                all_emails = set(pec_set) | set(mail_set)
                confirmed = verify_emails_on_site(
                    site,
                    all_emails,
                    timeout=max(8, int(args.timeout)),
                    max_pages=max(3, int(args.reliability_max_pages)),
                )
                return ({e for e in pec_set if e in confirmed}, {e for e in mail_set if e in confirmed})
            except Exception:
                # se la verifica fallisce per timeout/errore, manteniamo il risultato originale
                return pec_set, mail_set

        # elabora i risultati dello scraping e verifica quelli che hanno trovato email
        scrape_verified: dict[str, tuple[set, set]] = {}
        for c, scrape_result in results:
            if scrape_result is None:
                continue
            # Estrai pec e email dalla ScrapeResult e converti in set
            pec_found = {e.strip() for e in scrape_result.pec.split(" | ") if e.strip()}
            mail_found = {e.strip() for e in scrape_result.email.split(" | ") if e.strip()}
            pec_verified, mail_verified = _verify_scraped_result(c, pec_found, mail_found)
            if pec_verified or mail_verified:
                scrape_verified[c.codice_istat] = (pec_verified, mail_verified)

        verified_count = len([r for r in results if r[0].codice_istat in scrape_verified and r[1] is not None])
        scraped_count = len([r for r in results if r[1] is not None])
        if args.reliability_check and scraped_count > 0:
            print(f"      Verificati sul sito: {verified_count}/{scraped_count} comuni (affidabilità 80%+)")

        # Step 5: ricerca web per i comuni che lo scraping non ha trovato
        still_missing: list = [c for c, r in results if r is None]
        web_results: dict[str, tuple[set, set]] = {}

        def _accept_verified_web_result(c, pec_set: set[str], mail_set: set[str]) -> tuple[set[str], set[str]]:
            if not (pec_set or mail_set):
                return set(), set()
            if not args.reliability_check:
                return pec_set, mail_set

            site = site_by_istat.get(c.codice_istat, "")
            if not site:
                # senza sito ufficiale non possiamo verificare affidabilita:
                # manteniamo il risultato web come tentativo non confermato.
                return pec_set, mail_set

            try:
                from .reliability import verify_emails_on_site
                all_emails = set(pec_set) | set(mail_set)
                confirmed = verify_emails_on_site(
                    site,
                    all_emails,
                    timeout=max(8, int(args.timeout)),
                    max_pages=max(3, int(args.reliability_max_pages)),
                )
                return ({e for e in pec_set if e in confirmed}, {e for e in mail_set if e in confirmed})
            except Exception:
                return set(), set()
        if args.web_search and still_missing:
            print(f"      Ricerca web (Playwright + Chromium) per {len(still_missing)} comuni residui...")
            try:
                from .web_search import WebSearchFinder
                finder = WebSearchFinder()
                finder.start()
                try:
                    web_workers = min(args.workers, 4)

                    def _web_one(c):
                        site = site_by_istat.get(c.codice_istat, "")
                        from urllib.parse import urlparse

                        host = ""
                        if site:
                            u = site if site.startswith("http") else "https://" + site
                            host = urlparse(u).netloc.lstrip("www.")
                        try:
                            pec, mail = finder.search_polizia_locale(
                                c.nome,
                                c.provincia,
                                domain_hint=host,
                                extra_queries=args.extra_query or None,
                                strict_pl_local=args.strict,
                            )
                            pec, mail = _accept_verified_web_result(c, pec, mail)
                            return c, (pec, mail)
                        except Exception:
                            return c, (set(), set())

                    with ThreadPoolExecutor(max_workers=web_workers) as pool:
                        futures = [pool.submit(_web_one, c) for c in still_missing]
                        for fut in tqdm(
                            as_completed(futures),
                            total=len(futures),
                            desc="Web search",
                            unit="comune",
                        ):
                            c, (pec, mail) = fut.result()
                            if pec or mail:
                                web_results[c.codice_istat] = (pec, mail)
                finally:
                    finder.stop()
            except Exception as e:
                print(f"      Avviso: ricerca web non disponibile ({e})")

        if args.pm_source and still_missing:
            # fonte aggiuntiva: directory poliziamunicipale.it, poi filtro di coerenza dominio
            remaining = [c for c in still_missing if c.codice_istat not in web_results]
            if remaining:
                try:
                    from urllib.parse import urlparse
                    from .pm_registry import PoliziaMunicipaleFinder

                    def _domain_ok(email: str, host: str) -> bool:
                        if not host:
                            return True
                        domain = email.split("@", 1)[1].lower() if "@" in email else ""
                        return (
                            domain == host
                            or domain.endswith("." + host)
                            or host.endswith("." + domain)
                            or (
                                domain.startswith("pec.")
                                and host.split(".")[1:] == domain.split(".")[2:]
                            )
                        )

                    print(
                        f"      Fonte poliziamunicipale.it per {len(remaining)} comuni residui..."
                    )
                    pm_finder = PoliziaMunicipaleFinder(timeout=max(8, int(args.timeout)))
                    try:
                        show_tqdm_pm = sys.stderr.isatty()
                        iterable_pm = (
                            tqdm(remaining, desc="PM source", unit="comune")
                            if show_tqdm_pm
                            else remaining
                        )
                        for idx, c_pm in enumerate(iterable_pm, start=1):
                            site = site_by_istat.get(c_pm.codice_istat, "")
                            host = ""
                            if site:
                                u = site if site.startswith("http") else "https://" + site
                                host = urlparse(u).netloc.lstrip("www.")

                            pec, mail, _source = pm_finder.search_polizia_locale(
                                c_pm.nome, c_pm.provincia, strict_pl_local=args.strict
                            )
                            if host:
                                pec = {e for e in pec if _domain_ok(e, host)}
                                mail = {e for e in mail if _domain_ok(e, host)}

                            pec, mail = _accept_verified_web_result(c_pm, pec, mail)

                            if pec or mail:
                                web_results[c_pm.codice_istat] = (pec, mail)

                            if not show_tqdm_pm and (idx % 5 == 0 or idx == len(remaining)):
                                print(f"      PM source: {idx}/{len(remaining)}")
                    finally:
                        pm_finder.close()
                except Exception as e:
                    print(f"      Avviso: fonte poliziamunicipale.it non disponibile ({e})")

        for c, res in results:
            if res:
                # usa la versione verificata se disponibile
                if c.codice_istat in scrape_verified:
                    pec_set, mail_set = scrape_verified[c.codice_istat]
                    d = {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": f"Comune di {c.nome}",
                        "descrizione_uo": "Polizia Locale (dal sito comunale - verificato)",
                        "pec": " | ".join(sorted(pec_set)),
                        "email": " | ".join(sorted(mail_set)),
                        "sito": site_by_istat.get(c.codice_istat, ""),
                        "fonte": "WebScraping+Verifica",
                    }
                    rows.append(d)
                    continue
                
                # altrimenti usa il risultato grezzo dello scraping
                d = res.as_dict()
                d.update(
                    {
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                    }
                )
                rows.append(d)
                continue
            # Step 5b: usa risultato web search se disponibile
            web = web_results.get(c.codice_istat)
            if web and (web[0] or web[1]):
                pec_set, mail_set = web
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": f"Comune di {c.nome}",
                        "descrizione_uo": "Polizia Locale (da ricerca web)",
                        "pec": " | ".join(sorted(pec_set)),
                        "email": " | ".join(sorted(mail_set)),
                        "sito": site_by_istat.get(c.codice_istat, ""),
                        "fonte": "WebSearch",
                    }
                )
                continue
            # Fallback PEC del comune (opt-in)
            info = pec_comune_by_istat.get(c.codice_istat)
            if args.include_comune_pec and info and (info.get("pec_comune") or info.get("mail_comune")):
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": info.get("denominazione", f"Comune di {c.nome}"),
                        "descrizione_uo": "PEC generica del Comune (la PL non risulta avere un indirizzo dedicato)",
                        "pec": info.get("pec_comune", ""),
                        "email": info.get("mail_comune", ""),
                        "indirizzo": info.get("indirizzo", ""),
                        "cap": info.get("cap", ""),
                        "sito": info.get("sito", ""),
                        "fonte": "IndicePA-Comune (fallback)",
                    }
                )
            else:
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": f"Comune di {c.nome}",
                        "pec": "",
                        "email": "",
                        "fonte": "NON TROVATO",
                    }
                )
        for c in skipped:
            info = pec_comune_by_istat.get(c.codice_istat) if args.include_comune_pec else None
            if info and (info.get("pec_comune") or info.get("mail_comune")):
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": info.get("denominazione", f"Comune di {c.nome}"),
                        "descrizione_uo": "PEC generica del Comune (la PL non risulta avere un indirizzo dedicato)",
                        "pec": info.get("pec_comune", ""),
                        "email": info.get("mail_comune", ""),
                        "indirizzo": info.get("indirizzo", ""),
                        "cap": info.get("cap", ""),
                        "sito": info.get("sito", ""),
                        "fonte": "IndicePA-Comune (fallback)",
                    }
                )
            else:
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": f"Comune di {c.nome}",
                        "pec": "",
                        "email": "",
                        "fonte": "NON TROVATO (scrape-limit)",
                    }
                )
    else:
        # senza scraping: aggiungiamo righe per i comuni senza match
        for c in missing:
            info = None
            if args.include_comune_pec and enti_idx is None:
                # carichiamo on-demand se non lo avevamo ancora fatto
                try:
                    enti_idx = load_enti_index()
                except Exception:
                    enti_idx = {}
            if args.include_comune_pec and enti_idx:
                # cerchiamo per codice ISTAT
                for inf in enti_idx.values():
                    if inf.get("codice_istat") == c.codice_istat:
                        info = inf
                        break
            if info and (info.get("pec_comune") or info.get("mail_comune")):
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": info.get("denominazione", f"Comune di {c.nome}"),
                        "descrizione_uo": "PEC generica del Comune (la PL non risulta avere un indirizzo dedicato)",
                        "pec": info.get("pec_comune", ""),
                        "email": info.get("mail_comune", ""),
                        "indirizzo": info.get("indirizzo", ""),
                        "cap": info.get("cap", ""),
                        "sito": info.get("sito", ""),
                        "fonte": "IndicePA-Comune (fallback)",
                    }
                )
            else:
                rows.append(
                    {
                        "comune": c.nome,
                        "codice_istat": c.codice_istat,
                        "provincia": c.provincia,
                        "sigla_provincia": c.sigla_provincia,
                        "regione": c.regione,
                        "denominazione_ente": f"Comune di {c.nome}",
                        "pec": "",
                        "email": "",
                        "fonte": "NON TROVATO",
                    }
                )
        if missing:
            print(
                f"[4/4] Saltato scraping (disabilitato). {len(missing)} comuni senza UO PL dedicata."
            )

    # esportazione
    region_slug = (
        region_name.lower()
        .replace("'", "")
        .replace("/", "-")
        .replace(" ", "-")
        .replace("ü", "u")
        .replace("é", "e")
    )
    basename = f"polizia_locale_{region_slug}"
    paths = export_all(rows, args.output, basename)
    rows_with = [r for r in rows if r.get("pec") or r.get("email")]
    n_not_found = sum(1 for r in rows if "NON TROVATO" in r.get("fonte", ""))
    print("\n=== RISULTATI ===")
    print(f"  Comuni totali nella regione:           {len(comuni)}")
    print(f"  Comuni con mail/PEC PL pubblica:       {len(set(r['codice_istat'] for r in rows_with))}")
    print(f"  Comuni senza mail PL pubblica:         {n_not_found}")
    print(f"  Record totali esportati:               {len(rows)}")
    if n_not_found > 0 and not args.include_comune_pec:
        print(
            "\n  NOTA: lo script restituisce SOLO mail con local-part PL-specifica "
            "(polizialocale@, vigili@, comandopm@, …).\n"
            "  Per i comuni 'NON TROVATO' la Polizia Locale non ha una casella mail "
            "pubblica e dedicata.\n"
            "  Se vuoi includere come fallback la PEC istituzionale del Comune "
            "(comune.X@postacert.regione.it), riesegui con `--include-comune-pec`."
        )
    print("\nFile generati:")
    for k, v in paths.items():
        print(f"  - {k.upper():5s} {v}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="polizia-locale-finder",
        description=(
            "Cerca PEC ed email ufficiali della Polizia Locale/Municipale per tutti "
            "i comuni di una regione italiana (fonte ufficiale: IndicePA)."
        ),
    )
    p.add_argument(
        "region",
        nargs="?",
        help="Nome o codice ISTAT della regione (es. Lombardia, 03). "
        "Se omesso, parte la modalità interattiva.",
    )
    p.add_argument(
        "-o",
        "--output",
        default="./output",
        type=Path,
        help="Cartella di output (default: ./output)",
    )
    p.add_argument(
        "--no-scrape",
        dest="scrape",
        action="store_false",
        help="Disabilita lo scraping dei siti comunali (usa solo IndicePA).",
    )
    p.add_argument(
        "--no-expand-unioni",
        dest="expand_unioni",
        action="store_false",
        help="Disabilita l'espansione delle Unioni di Comuni / Consorzi PL. "
        "Per default è ATTIVA: identifica le UO/AOO di Polizia Locale gestite "
        "da Unioni di Comuni o Consorzi e replica la PEC sui comuni aderenti.",
    )
    p.set_defaults(expand_unioni=True)
    p.add_argument(
        "--list-regions",
        action="store_true",
        help="Stampa l'elenco delle 20 regioni e termina.",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Pausa (sec) tra richieste di scraping in modalità sequenziale (default: 0.5)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Timeout HTTP per richiesta di scraping (default: 15s)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Numero di thread paralleli per lo scraping (default: 8, 1 = sequenziale)",
    )
    p.add_argument(
        "--extra-query",
        dest="extra_query",
        action="append",
        default=[],
        help="Query aggiuntiva da provare per ogni comune (ripetibile). Esempio: --extra-query 'polizia locale {comune} email'",
    )
    p.add_argument(
        "--scrape-limit",
        type=int,
        default=0,
        dest="scrape_limit",
        help="Limita lo scraping ai primi N comuni senza match IndicePA "
        "(0 = nessun limite, default). Utile per validazione su regioni grandi.",
    )
    p.add_argument(
        "--no-comune-pec",
        dest="include_comune_pec",
        action="store_false",
        help="(default) Non includere la PEC istituzionale del Comune come "
        "fallback quando manca una mail/PEC PL-specifica.",
    )
    p.add_argument(
        "--include-comune-pec",
        dest="include_comune_pec",
        action="store_true",
        help="Includi la PEC istituzionale del Comune come fallback quando "
        "manca una mail/PEC PL-specifica. Marcata come 'PEC generica del Comune'.",
    )
    p.set_defaults(include_comune_pec=False)
    p.add_argument(
        "--no-web-search",
        dest="web_search",
        action="store_false",
        help="Disabilita la ricerca web via Playwright + Chromium per i comuni "
        "in cui lo scraping diretto non trova mail PL-specifiche. "
        "Per default è ATTIVA.",
    )
    p.set_defaults(web_search=True)
    p.add_argument(
        "--pm-source",
        action="store_true",
        help="Usa anche poliziamunicipale.it/comuni come fonte aggiuntiva per i comuni senza risultati.",
    )
    p.add_argument(
        "--no-reliability-check",
        dest="reliability_check",
        action="store_false",
        help="Disabilita la verifica mail/PEC sul sito ufficiale del comune per i risultati da fonti web.",
    )
    p.set_defaults(reliability_check=True)
    p.add_argument(
        "--reliability-max-pages",
        type=int,
        default=8,
        help="Numero massimo di pagine del sito comunale da controllare per confermare una mail/PEC (default: 8).",
    )
    p.add_argument(
        "--no-pdf",
        dest="pdf_extract",
        action="store_false",
        help="Disabilita l'estrazione di mail dai PDF linkati nelle pagine "
        "comunali. Per default è ATTIVA (alcuni comuni espongono la mail "
        "della PL solo in ordinanze/organigrammi PDF).",
    )
    p.set_defaults(pdf_extract=True)
    p.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="(opzionale) Disabilita il filtro 'solo mail PL-specifiche': "
        "accetta qualunque mail registrata in IndicePA come UO della PL, "
        "anche le PEC generiche del Comune.",
    )
    p.set_defaults(strict=True)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_regions:
        for code, name in list_regions():
            print(f"[{code}] {name}")
        return 0

    if args.region:
        match = resolve_region(args.region)
        if not match:
            print(
                f"Regione '{args.region}' non riconosciuta. Usa --list-regions per l'elenco.",
                file=sys.stderr,
            )
            return 2
        code, name = match
    else:
        code, name = _interactive_pick_region()

    return _run(code, name, args)


if __name__ == "__main__":
    raise SystemExit(main())
