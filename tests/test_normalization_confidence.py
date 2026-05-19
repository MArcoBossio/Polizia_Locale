from __future__ import annotations

from polizia_locale.confidence import apply_confidence
from polizia_locale.normalization import (
  canonical_commune_name,
  canonical_email_key,
  commune_variants,
  is_close_match,
)
from polizia_locale.pm_registry import PoliziaMunicipaleFinder
from polizia_locale.pdf_extractor import find_pdf_links_broad


def test_normalization_handles_abbreviations_bilingual_and_apostrophes():
    assert canonical_commune_name("S. Giovanni Lupatoto") == "san giovanni lupatoto"
    assert is_close_match("Bozen", "Bolzano")
    assert is_close_match("Laives-Leifers", "Laives")
    variants = commune_variants("Bolzano")
    assert "bozen" in variants


def test_confidence_parses_composite_sources_and_rewards_pl_context():
    rows = [
        {
            "comune": "Bolzano",
            "denominazione_ente": "Comune di Bolzano",
            "descrizione_uo": "Polizia Locale",
            "pec": "",
            "email": "polizialocale@comune.bolzano.it",
            "sito": "https://comune.bolzano.it",
            "fonte": "IndicePA + PEC Comune",
            "matched_by": "context_polizia | pdf",
        }
    ]

    scored = apply_confidence(rows)

    assert scored[0]["confidence"] >= 0.95
    assert "source:IndicePA + PEC Comune" in scored[0]["matched_by"]
    assert "context_polizia" in scored[0]["matched_by"]

def test_confidence_rewards_site_domain_match():
    rows = [
        {
            "comune": "Prato",
            "denominazione_ente": "Comune di Prato",
            "descrizione_uo": "Polizia Locale",
            "pec": "",
            "email": "polizialocale@comune.prato.it",
            "sito": "https://www.comune.prato.it",
            "fonte": "ScrapingSitoComune",
            "matched_by": "context_polizia",
        }
    ]

    scored = apply_confidence(rows)

    assert scored[0]["confidence"] > 0.9
    assert "site_match" in scored[0]["matched_by"]


def test_canonical_email_key_deduplicates_punctuation_variants():
    assert canonical_email_key("polizia-locale@comune.example.it") == canonical_email_key("polizia.locale@comune.example.it")


def test_find_pdf_links_broad_includes_contact_pdfs():
    html = """
    <html><body>
      <a href="/files/organigramma.pdf">Organigramma Polizia Locale</a>
      <a href="/files/regolamento.pdf">Regolamento</a>
      <a href="/files/atto.pdf">Atto generico</a>
    </body></html>
    """

    links = find_pdf_links_broad(html, "https://www.comune.example.it", limit=3)

    assert links[0].endswith("organigramma.pdf")
    assert any(link.endswith("regolamento.pdf") for link in links)


def test_poliziamunicipale_finder_matches_bilingual_commune_name(monkeypatch):
    finder = PoliziaMunicipaleFinder(timeout=1)
    html = """
    <html><body>
      <table>
        <tr>
          <td><a href="/scheda/bolzano">Scheda</a></td>
          <td>Bozen</td>
          <td>BZ</td>
        </tr>
      </table>
    </body></html>
    """
    monkeypatch.setattr(finder, "_fetch_letter_index", lambda letter: html)

    detail_url = finder._find_detail_url("Bolzano", "BZ")

    assert detail_url.endswith("/scheda/bolzano")
    finder.close()