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
  - `python run.py "Valle d'Aosta" --no-scrape` → 18 UO PL/PM trovate su 74 comuni.
  - `echo 20 | python run.py --no-scrape` (Sardegna) → 65 UO PL/PM su 377 comuni.
  - `python run.py Lombardia --scrape-limit 15 --workers 8 --include-comune-pec`
    → **1.500 / 1.502 comuni coperti (99,9 %)** in ~2 minuti.

## Aggiornamenti del 11/05 (validazione Lombardia + Unioni)
- **Bug fix `load_enti_index`**: ora filtra `Codice_Categoria == "L6"` per
  prendere il sito istituzionale del Comune e non quello di scuole/ASL.
- **Scraper robusto**: split timeout (connect/read), budget temporale per
  comune (25 s), max 4 pagine candidate, gestione 202 di DuckDuckGo.
- **Scraping parallelo**: opzione `--workers N` (default 8, ThreadPoolExecutor).
- **Opzione `--scrape-limit N`** per test/validazione su regioni grandi.
- **Opzione `--no-comune-pec`**: per default è ATTIVO il fallback con la PEC
  istituzionale del Comune dal dataset Enti.
- **AOO dataset di IndicePA** integrato come fonte primaria addizionale.
- **URL pattern guessing nello scraper**: prova direttamente
  `/polizia-locale`, `/comando-polizia-locale`, `/vigili-urbani`, ecc.
- **Espansione Unioni di Comuni / Consorzi PL** (`--no-expand-unioni` per
  disabilitare): identifica gli enti `L18`/`L12`/`L36` che hanno una UO/AOO di
  Polizia Locale, scrapa il loro sito istituzionale per estrarre i comuni
  aderenti (text-search inverso sulla lista ISTAT della regione, evita falsi
  positivi cross-regione filtrando per sede dell'Unione nella stessa regione),
  e replica la PEC della PL su tutti i comuni membri.

## Risultati tipici (default config, comando `python run.py <regione>`)

| Regione    | Comuni | UO    | AOO | Unioni | Fallback | NON TROVATO | Coverage |
|------------|-------:|------:|----:|-------:|---------:|------------:|---------:|
| Lombardia  | 1.502  | 469   | 6   | 15     | 1.046    | 1           | 99,9 %   |
| Toscana    |   273  | 166   | 0   | 24     |    92    | 0           | 100 %    |
| V. d'Aosta |    74  |  18   | 0   |  0     |    56    | 0           | 100 %    |

## Backlog (P2)
- Validazione MX delle PEC scoperte via scraping.
- Cache persistente dei risultati di scraping per ripartire da dove ci si è fermati.
- Modulo opzionale di invio batch PEC (SQLite tracking, rate limiting).

## Next tasks
- Possibile modulo `--send-pec` per trasformare lo script in strumento operativo end-to-end.
