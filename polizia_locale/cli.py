"""CLI principale per la ricerca delle PEC/Email della Polizia Locale."""
from __future__ import annotations

import argparse
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
    found_uo = find_polizia_locale_uo(istat_codes)
    found_aoo = find_polizia_locale_aoo(istat_codes)
    print(
        f"      {len(found_uo)} UO + {len(found_aoo)} AOO 'Polizia Locale/Municipale' "
        f"trovate su IndicePA."
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
                if not matched:
                    matched = match_member_comuni(homepage, comuni)
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
                # alla stessa provincia (le Unioni sono per legge sub-provinciali)
                sigla_unione = sigla_by_istat.get(u.codice_comune_istat_sede, "")
                for c in matched:
                    if c.codice_istat in by_istat:
                        continue  # ha già una PL diretta
                    if sigla_unione and c.sigla_provincia != sigla_unione:
                        continue  # diversa provincia → falso positivo
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

        for c, res in results:
            if res:
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
    print("\n=== RISULTATI ===")
    print(f"  Comuni totali: {len(comuni)}")
    print(f"  Comuni con PEC/Mail Polizia Locale: {len(set(r['codice_istat'] for r in rows_with))}")
    print(f"  Record totali (PL/PM trovate): {len(rows_with)}")
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
        help="Disabilita il fallback con la PEC istituzionale del Comune. "
        "Per default, se la Polizia Locale non ha una mail/PEC dedicata, viene "
        "restituita la PEC del Comune (marcata come 'PEC generica del Comune').",
    )
    p.set_defaults(include_comune_pec=True)
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
