from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
import signal
import time

from polizia_locale.regions import list_regions


ROOT_DIR = Path(__file__).parent
PROJECT_ROOT = ROOT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
load_dotenv(ROOT_DIR / ".env")
BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY")
BACKEND_KILL_TIMEOUT = int(os.environ.get("BACKEND_KILL_TIMEOUT", "5"))

app = FastAPI(title="Polizia Locale Dashboard API")
api_router = APIRouter(prefix="/api")


class ScrapeRequest(BaseModel):
    region: str = Field(..., min_length=1)
    include_comune_pec: bool = False
    web_search: bool = True
    pm_source: bool = True
    strict: bool = True
    scrape_limit: int = 0
    workers: int = 0


@dataclass
class ScrapeJob:
    id: str
    region: str
    command: list[str]
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    output_slug: str | None = None
    process: Optional[subprocess.Popen] = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


_JOB_LOCK = threading.Lock()
_JOBS: dict[str, ScrapeJob] = {}


@api_router.get("/")
async def root():
    return {"message": "Polizia Locale dashboard API"}


@api_router.get("/regions")
async def get_regions():
    return {"regions": [{"code": code, "name": name} for code, name in list_regions()]}


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


def _build_scrape_command(payload: ScrapeRequest) -> list[str]:
    command = [sys.executable, str(PROJECT_ROOT / "run.py"), payload.region, "-o", str(OUTPUT_DIR)]
    if not payload.web_search:
        command.append("--no-web-search")
    if not payload.pm_source:
        command.append("--no-pm-source")
    if payload.include_comune_pec:
        command.append("--include-comune-pec")
    if not payload.strict:
        command.append("--no-strict")
    if payload.scrape_limit and payload.scrape_limit > 0:
        command.extend(["--scrape-limit", str(payload.scrape_limit)])
    if payload.workers and payload.workers > 0:
        command.extend(["--workers", str(payload.workers)])
    return command


def _job_snapshot(job: ScrapeJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "region": job.region,
        "command": job.command,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "exit_code": job.exit_code,
        "output_slug": job.output_slug,
        "stdout": job.stdout,
        "stderr": job.stderr,
        "error": job.error,
    }


def _run_scrape_job(job_id: str, payload: ScrapeRequest):
    command = _build_scrape_command(payload)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    with _JOB_LOCK:
        job = _JOBS[job_id]
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
        job.command = command

    try:
        popen_kwargs: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(command, **popen_kwargs)
        with _JOB_LOCK:
            job = _JOBS[job_id]
            job.process = proc

        stdout, stderr = proc.communicate()
        returncode = proc.returncode

        with _JOB_LOCK:
            job = _JOBS[job_id]
            job.exit_code = returncode
            job.stdout = (stdout or "")[-20000:]
            job.stderr = (stderr or "")[-20000:]
            job.process = None
            job.status = "completed" if returncode == 0 else "failed"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            if returncode == 0:
                outputs = _list_output_files()
                job.output_slug = outputs[0]["slug"] if outputs else None
            if returncode != 0 and not job.stderr:
                job.error = "Scraping terminato con errore"
    except Exception as exc:  # pragma: no cover - defensive
        with _JOB_LOCK:
            job = _JOBS[job_id]
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.error = str(exc)


@api_router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    # optional API key check
    if BACKEND_API_KEY:
        key = request.headers.get("x-api-key")
        if key != BACKEND_API_KEY:
            raise HTTPException(status_code=401, detail="API key non valida")
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job non trovato")
        if job.status != "running":
            raise HTTPException(status_code=400, detail="Job non in esecuzione")
        if job.process:
            pid = job.process.pid
            try:
                # request graceful termination
                try:
                    job.process.terminate()
                except Exception:
                    pass

                # wait for process to exit up to timeout
                waited = 0.0
                interval = 0.1
                while waited < BACKEND_KILL_TIMEOUT:
                    if job.process.poll() is not None:
                        break
                    time.sleep(interval)
                    waited += interval

                if job.process.poll() is None:
                    # still alive => force kill the process tree
                    try:
                        if os.name == "nt":
                            # taskkill /T /F to kill process tree on Windows
                            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False)
                        else:
                            try:
                                pgid = os.getpgid(pid)
                                os.killpg(pgid, signal.SIGKILL)
                            except Exception:
                                os.kill(pid, signal.SIGKILL)
                    except Exception as exc:
                        raise HTTPException(status_code=500, detail=f"Forza-kill fallito: {exc}")

                job.status = "cancelled"
                job.finished_at = datetime.now(timezone.utc).isoformat()
                return {"status": "cancelled"}
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
        else:
            raise HTTPException(status_code=400, detail="Processo non disponibile")


@api_router.get("/jobs")
async def list_jobs():
    with _JOB_LOCK:
        jobs = sorted(_JOBS.values(), key=lambda item: item.created_at, reverse=True)
        return {"jobs": [_job_snapshot(job) for job in jobs]}


@api_router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job non trovato")
        return _job_snapshot(job)


@api_router.post("/scrape", status_code=202)
async def start_scrape(payload: ScrapeRequest, request: Request):
    # optional API key check
    if BACKEND_API_KEY:
        key = request.headers.get("x-api-key")
        if key != BACKEND_API_KEY:
            raise HTTPException(status_code=401, detail="API key non valida")
    # prevent concurrent runs
    with _JOB_LOCK:
        for j in _JOBS.values():
            if j.status == "running":
                raise HTTPException(status_code=409, detail="Un job è già in esecuzione")
        job_id = uuid.uuid4().hex
        command = _build_scrape_command(payload)
        job = ScrapeJob(id=job_id, region=payload.region, command=command)
        _JOBS[job_id] = job
    threading.Thread(target=_run_scrape_job, args=(job_id, payload), daemon=True).start()
    return _job_snapshot(job)


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