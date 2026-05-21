from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware


ROOT_DIR = Path(__file__).parent
PROJECT_ROOT = ROOT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
load_dotenv(ROOT_DIR / ".env")

app = FastAPI(title="Polizia Locale Dashboard API")
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"message": "Polizia Locale dashboard API"}


def _read_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def _summarize_rows(rows: list[dict]) -> dict:
    total = len(rows)
    with_contact = [row for row in rows if (row.get("pec") or row.get("mail"))]
    not_found = [row for row in rows if "NON TROVATO" in (row.get("fonte") or "")]
    confidence_values: list[float] = []
    for row in rows:
        try:
            confidence_values.append(float(row.get("confidence", 0) or 0))
        except Exception:
            continue
    avg_confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0
    return {
        "total_rows": total,
        "rows_with_contact": len(with_contact),
        "rows_without_contact": total - len(with_contact),
        "not_found_rows": len(not_found),
        "avg_confidence": avg_confidence,
    }


def _output_metadata(path: Path) -> dict:
    rows = _read_json(path)
    stat = path.stat()
    stem = path.stem
    return {
        "slug": stem,
        "label": stem.removeprefix("polizia_locale_").replace("-", " ").title(),
        "json_path": str(path),
        "csv_path": str(path.with_suffix(".csv")),
        "xlsx_path": str(path.with_suffix(".xlsx")),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
        "summary": _summarize_rows(rows),
    }


def _list_output_files() -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    files = [path for path in OUTPUT_DIR.glob("polizia_locale_*.json") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [_output_metadata(path) for path in files]


@api_router.get("/outputs")
async def list_outputs():
    return {"files": _list_output_files()}


@api_router.get("/outputs/latest")
async def latest_output():
    files = _list_output_files()
    if not files:
        raise HTTPException(status_code=404, detail="Nessun output disponibile")
    return files[0]


@api_router.get("/outputs/{slug}")
async def get_output(slug: str):
    path = OUTPUT_DIR / f"{slug}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output non trovato")
    rows = _read_json(path)
    return {"file": _output_metadata(path), "summary": _summarize_rows(rows), "rows": rows}


@api_router.get("/outputs/{slug}/download/{kind}")
async def download_output(slug: str, kind: str):
    kind = kind.lower().strip()
    if kind not in {"json", "csv", "xlsx"}:
        raise HTTPException(status_code=400, detail="Formato non supportato")
    path = OUTPUT_DIR / f"{slug}.{kind}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File non trovato")
    media_type = {
        "json": "application/json",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[kind]
    return FileResponse(path, media_type=media_type, filename=path.name)


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)