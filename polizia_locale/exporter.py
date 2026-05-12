"""Esportazione risultati in CSV, XLSX e JSON."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

FIELDS = [
    "comune",
    "provincia",
    "sigla_provincia",
    "mail",
    "pec",
]


def _normalize_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {
                "comune": r.get("comune", ""),
                "provincia": r.get("provincia", ""),
                "sigla_provincia": r.get("sigla_provincia", r.get("sigla", "")),
                "mail": r.get("email", r.get("mail", "")),
                "pec": r.get("pec", ""),
            }
        )
    return out


def export_all(rows: list[dict], out_dir: Path, basename: str) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_rows(rows)

    csv_path = out_dir / f"{basename}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(normalized)

    json_path = out_dir / f"{basename}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    xlsx_path = out_dir / f"{basename}.xlsx"
    df = pd.DataFrame(normalized, columns=FIELDS)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Polizia Locale")
        # auto-fit larghezza colonne
        ws = writer.sheets["Polizia Locale"]
        for i, col in enumerate(FIELDS, start=1):
            max_len = max([len(str(col))] + [len(str(v)) for v in df[col].head(500)])
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(
                max_len + 2, 60
            )

    return {"csv": str(csv_path), "json": str(json_path), "xlsx": str(xlsx_path)}
