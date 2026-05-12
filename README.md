# Polizia Locale / Municipale — PEC & Mail Finder

Script CLI in Python che, data una **regione italiana**, recupera l'elenco di
**tutti i comuni** (fonte ISTAT) e cerca esclusivamente le **email ufficiali e
le PEC della Polizia Locale / Municipale**.

Niente di più, niente di meno: il risultato viene filtrato per il termine di
ricerca "polizia locale", "polizia municipale", "vigili urbani" o varianti
equivalenti, ed escludendo Polizia di Stato, Stradale, Provinciale, ecc.

## Fonti dati

1. **ISTAT** — elenco ufficiale dei comuni italiani (CSV, aggiornato semestralmente).
   <https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv>
2. **IndicePA (AgID)** — registro ufficiale delle Pubbliche Amministrazioni italiane:
   - **Unità Organizzative (UO)** — PEC/email dei comandi di Polizia Locale.
   - **Aree Organizzative Omogenee (AOO)** — alcuni comuni registrano la PL come AOO.
   - **Enti L18/L12/L36** — Unioni di Comuni, Comunità Montane, Consorzi che gestiscono la PL in forma **associata**. Per ognuno lo script scrapa il sito istituzionale per identificare i comuni aderenti e replica la PEC della PL su tutti i membri della regione.
   - **Enti L6** — usato per la PEC istituzionale del Comune come fallback finale.
3. **Scraping del sito comunale** (fallback) — per i comuni che in IndicePA non
   hanno né una UO né una AOO né un'Unione di PL, lo script visita
   direttamente i path comuni tipo `/polizia-locale`, `/comando-polizia-locale`,
   `/vigili-urbani`.
4. **PEC del Comune come fallback** (attivo di default) — se nessuna delle fonti
   precedenti restituisce una mail/PEC specifica della Polizia Locale, lo
   script usa la PEC istituzionale del Comune dal dataset Enti, marcata
   `IndicePA-Comune (fallback)`. Disabilitabile con `--no-comune-pec`.

## Installazione

```bash
pip install -r requirements-cli.txt
playwright install chromium       # solo se userai il backend Bing+Playwright
```

### Configurazione (opzionale ma raccomandata)

Crea un file `.env` nella root del progetto:

```bash
# Brave Search API key (gratis 2.000 query/mese — https://brave.com/search/api/)
BRAVE_API_KEY=tua_chiave_qui
```

Se il file `.env` contiene `BRAVE_API_KEY`, lo script userà automaticamente
Brave Search per il livello 6 (molto più veloce). In assenza della chiave
cade su Playwright+Bing.

## Utilizzo

Tre modalità:

```bash
# 1) argomento da riga di comando (nome o codice ISTAT)
python run.py Lombardia
python run.py 03

# 2) menu interattivo con elenco numerato delle 20 regioni
python run.py

# 3) elenco regioni
python run.py --list-regions
```

### Opzioni utili

```text
-o, --output DIR        Cartella di output (default: ./output)
--no-scrape             Disabilita lo scraping dei siti comunali
--no-expand-unioni      Disabilita l'espansione delle Unioni/Consorzi PL
--no-pdf                Disabilita l'estrazione di mail dai PDF allegati
--no-strict             Accetta anche PEC generiche del Comune da IndicePA
--workers N             Thread paralleli (default 8)
--scrape-limit N        Limita lo scraping ai primi N comuni mancanti
--include-comune-pec    Includi la PEC istituzionale del Comune come
                        fallback per i comuni senza PL-specifica (OFF di default)
--timeout SEC           Timeout HTTP scraping (default 15)
```

## Dove trovo i risultati

Default: cartella `./output/` (relativa alla directory in cui lanci il comando).

Esempio dopo `git clone`:
```bash
git clone <url-repo> polizia-locale-finder
cd polizia-locale-finder
pip install -r requirements-cli.txt
python run.py Toscana
ls ./output/
# polizia_locale_toscana.csv
# polizia_locale_toscana.xlsx
# polizia_locale_toscana.json
```

### Esempio reale Toscana (273 comuni, ~22 min, strict mode + Brave)

| Fonte                              | Comuni | Esempio mail/PEC |
|------------------------------------|-------:|------------------|
| `IndicePA` (PL diretta)            |     64 | `direz.pol.municipale@pec.comune.fi.it`, `polizialocale@comune.grosseto.it` |
| `IndicePA-Unione` (PL associata)   |     17 | `polizialocale.unionevaldera@postacert.toscana.it` |
| `ScrapingSitoComune` (BFS sito)    |     43 | `polizialocale@comune.prato.it`, `centraleoperativapm@comune.lucca.it` |
| `WebSearch` (Brave API)            |     51 | `poliziamunicipale@comune.massa.ms.it`, `polizialocale@comune.pistoia.it` |
| `NON TROVATO`                      |     99 | (no mail PL pubblica) |
| **Coverage strict**                | **174/273 (64 %)** | mail genuinamente PL-specifiche |

**Tutti i 10 capoluoghi della Toscana coperti**: Firenze, Prato, Pisa, Livorno,
Siena, Arezzo, Pistoia, Lucca, Massa, Grosseto.

### Comportamento per i comuni "NON TROVATO"

Significa che la Polizia Locale di quel comune **non ha una casella mail
pubblicamente esposta** né su IndicePA né sul sito istituzionale (in HTML
plain). Per quei comuni puoi:

1. Rilanciare con `--include-comune-pec` per usare come fallback la PEC
   istituzionale del Comune (es. `comune.X@postacert.regione.it`).
2. Aprire la PEC istituzionale del Comune e nell'oggetto specificare
   "Alla c.a. Polizia Locale" — è il canale legalmente valido.

## Output

Per ogni esecuzione vengono prodotti tre file con lo stesso contenuto:

- `output/polizia_locale_<regione>.csv`
- `output/polizia_locale_<regione>.xlsx`
- `output/polizia_locale_<regione>.json`

Colonne:

| Campo                | Descrizione                                            |
|----------------------|--------------------------------------------------------|
| comune               | Denominazione comune                                   |
| codice_istat         | Codice ISTAT (6 cifre)                                 |
| provincia            | Provincia / unità territoriale sovracomunale           |
| sigla_provincia      | Sigla auto della provincia                             |
| regione              | Regione                                                |
| denominazione_ente   | Nome ente in IndicePA (di norma "Comune di …")         |
| codice_ipa           | Codice IPA dell'ente                                   |
| codice_uni_uo        | Codice univoco UO (Polizia Locale) in IndicePA         |
| descrizione_uo       | Descrizione dell'UO (es. "Polizia Municipale")         |
| pec                  | Indirizzo/i PEC ufficiale/i                            |
| email                | Email ordinaria/e                                      |
| telefono / indirizzo | Recapiti UO                                            |
| sito / pagina        | Sito comunale e pagina dove è stato trovato (scraping) |
| fonte                | "IndicePA", "ScrapingSitoComune" o "NON TROVATO"       |

## Cache

I dataset ISTAT e IndicePA vengono scaricati una sola volta e tenuti in cache
per 24 ore (7 giorni per ISTAT) in:

```
~/.cache/polizia_locale/
```

Puoi sovrascrivere la cartella con la variabile d'ambiente
`POLIZIA_LOCALE_CACHE`.

## Note legali

I dataset di IndicePA sono open data pubblicati da AgID. Le PEC delle PA
italiane sono pubbliche per legge. Lo scraping dei siti comunali utilizza
contenuti pubblici e introduce una pausa tra le richieste; usalo
responsabilmente.
        |
| sito / pagina        | Sito comunale e pagina dove è stato trovato (scraping) |
| fonte                | "IndicePA", "ScrapingSitoComune" o "NON TROVATO"       |

## Cache

I dataset ISTAT e IndicePA vengono scaricati una sola volta e tenuti in cache
per 24 ore (7 giorni per ISTAT) in:

```
~/.cache/polizia_locale/
```

Puoi sovrascrivere la cartella con la variabile d'ambiente
`POLIZIA_LOCALE_CACHE`.

## Note legali

I dataset di IndicePA sono open data pubblicati da AgID. Le PEC delle PA
italiane sono pubbliche per legge. Lo scraping dei siti comunali utilizza
contenuti pubblici e introduce una pausa tra le richieste; usalo
responsabilmente.

## Licenza

MIT — usa liberamente, riconoscendo la paternità.

## Contributi

Pull request benvenute. Per problemi/bug aprire un issue indicando regione
testata e comune specifico in cui la mail PL non è stata trovata.
.
