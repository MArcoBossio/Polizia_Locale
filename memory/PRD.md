# PRD — Polizia Locale / Municipale PEC & Mail Finder

## Original problem statement (IT)
> Ho bisogno di uno script in python che mi permetta di inserire una determinata
> regione italiana, mi prende tutti i singoli comuni e mi cerca le mail
> ufficiali o le PEC, solamente della polizia locale o municipale, niente di
> più niente di meno.

## User choices
- Fonte comuni: **ISTAT** (CSV ufficiale).
- Fonte PEC/Email: **IndicePA** + **scraping** dei siti comunali come fallback.
- Output: **CSV + XLSX + JSON**.
- Interfaccia: **solo CLI Python**.
- Input regione: **argomento da riga di comando**, **interattivo**, **menu numerato**.

## Architettura

```
/app
├── run.py                       # entry point CLI
├── README.md                    # documentazione utente
├── requirements-cli.txt         # dipendenze (pandas, openpyxl, bs4, lxml, tqdm, requests)
└── polizia_locale/
    ├── __init__.py
    ├── __main__.py              # `python -m polizia_locale`
    ├── cli.py                   # argparse + menu interattivo + pipeline
    ├── regions.py               # 20 regioni italiane + alias resolver
    ├── comuni.py                # download/parse ISTAT CSV, filtro per regione
    ├── indicepa.py              # download dataset UO + Enti, filtro PL/PM
    ├── scraper.py               # fallback: DuckDuckGo + scraping siti comunali
    ├── exporter.py              # esportazione CSV / XLSX / JSON
    └── utils.py                 # cache su disco + download HTTP
```

## Implementazione (Feb 2026)

- **ISTAT CSV** scaricato da `https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv` (cache 7gg).
- **IndicePA** dataset "Unità Organizzative" (`unita-organizzative.xlsx`, cache 24h) filtrato per:
  - `Codice_comune_ISTAT` ∈ comuni della regione
  - `Descrizione_uo` regex per "polizia local/municipal", "vigili urbani", "comando PM/PL" — con esclusione di Polizia di Stato / Stradale / Provinciale / Mortuaria / Penitenziaria / Giudiziaria / Scientifica.
- Estrae fino a 5 coppie `MailN/Tipo_MailN` distinguendo **PEC** dalle email ordinarie.
- **Scraper di fallback** (`--scrape` di default, disattivabile con `--no-scrape`):
  - Recupera il sito istituzionale dal dataset "Enti" di IndicePA (campo `Sito_istituzionale`), altrimenti interroga DuckDuckGo HTML.
  - Visita homepage + link che contengono "polizia local/municipal/vigili urbani/contatti".
  - Estrae email con regex e le filtra in base al **contesto** (devono comparire entro 80 char da parole chiave Polizia Locale) oppure in base alla **local part** (`polizialocale@`, `poliziamunicipale@`, `vigili@`, `comandopm@` …).
  - Classifica come PEC quelle con domini `pec.*`, `legalmail`, `postacertificata`, `cert.*` o con "pec" nel contesto.
- **Output**: tre file omogenei (`polizia_locale_<regione>.{csv,xlsx,json}`) con 17 colonne.
- **Caching**: `~/.cache/polizia_locale/` (override via `POLIZIA_LOCALE_CACHE`).
- **Test reali eseguiti**:
  - `python run.py "Valle d'Aosta" --no-scrape` → 18 UO PL/PM trovate su 74 comuni (codici PEC dei rispettivi comandi).
  - `echo 20 | python run.py --no-scrape` (menu interattivo, Sardegna) → 65 UO PL/PM su 377 comuni.
  - Smoke test scraper su comune senza match → ritorna `None` senza errori.

## Backlog (P1/P2)
- P1: aggiungere CLI flag `--workers N` per scraping in parallelo (thread pool).
- P1: integrare anche il dataset **AOO** di IndicePA come ulteriore fonte primaria (alcuni enti registrano la PL come AOO invece che UO).
- P2: cache persistente dei risultati di scraping per riprendere esecuzioni interrotte.
- P2: opzione `--format` per scegliere uno solo dei formati di output.
- P2: integrare ulteriori motori di ricerca di fallback (Bing, Brave Search).
- P2: validazione MX della PEC scoperta via scraping.

## Next tasks
- Raccogliere feedback dall'utente su una regione grande (es. Lombardia) per affinare i pattern di estrazione e l'euristica del fallback.
