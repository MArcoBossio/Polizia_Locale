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
2. **IndicePA (AgID)** — registro ufficiale delle Pubbliche Amministrazioni
   italiane, dataset **Unità Organizzative** (contiene PEC/email per ogni UO,
   inclusi i comandi di Polizia Locale).
   <https://indicepa.gov.it/ipa-dati/dataset/unita-organizzative>
3. **Scraping del sito comunale** (fallback) — per i comuni che in IndicePA non
   hanno una UO dedicata alla Polizia Locale, lo script cerca il sito
   istituzionale e prova a estrarre email/PEC con contesto "polizia
   locale/municipale".

## Installazione

```bash
pip install -r requirements-cli.txt
```

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
--no-scrape             Usa solo IndicePA, niente scraping dei siti comunali
--workers N             Thread paralleli per lo scraping (default 8)
--scrape-limit N        Limita lo scraping ai primi N comuni mancanti (utile in fase di test)
--include-comune-pec    Per i comuni in cui la Polizia Locale non ha una mail/PEC
                        dedicata, usa come fallback la PEC istituzionale del
                        Comune dal dataset IndicePA Enti, marcata chiaramente.
--sleep SEC             Pausa tra richieste di scraping in modalità sequenziale (default 0.5)
--timeout SEC           Timeout HTTP scraping (default 15)
```

### Esempio reale

Lombardia (1.502 comuni, ~2 minuti totali con `--workers 8 --include-comune-pec`):

| Fonte                            | Comuni |
|----------------------------------|-------:|
| `IndicePA` (UO PL dedicata)      |    433 |
| `IndicePA-Comune (fallback)`     |  1.066 |
| `ScrapingSitoComune`             |      1 |
| `NON TROVATO`                    |      1 |
| **Totale copertura**             |  **1.500 / 1.502 (99,9 %)** |

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
