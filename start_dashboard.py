#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _build_backend_command(host: str, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.server:app",
        "--host",
        host,
        "--port",
        str(port),
        "--reload",
    ]


def _build_frontend_command(host: str, port: int, backend_url: str) -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    env["VITE_BACKEND_URL"] = backend_url
    return env, [_npm_command(), "run", "dev", "--", "--host", host, "--port", str(port)]


def _wait_for_http(url: str, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except urllib.error.URLError:
            time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Start backend + frontend for the Polizia Locale dashboard.")
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-host", default="127.0.0.1")
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--ready-timeout", type=int, default=60, help="Seconds to wait for both services to become ready.")
    parser.add_argument("--no-open-browser", action="store_true", help="Do not open the dashboard automatically.")
    args = parser.parse_args()

    backend_cmd = _build_backend_command(args.backend_host, args.backend_port)
    backend_env = os.environ.copy()
    backend_env["PYTHONPATH"] = str(ROOT) + os.pathsep + backend_env.get("PYTHONPATH", "")

    frontend_backend_url = f"http://{args.backend_host}:{args.backend_port}"
    frontend_env, frontend_cmd = _build_frontend_command(args.frontend_host, args.frontend_port, frontend_backend_url)

    processes: list[subprocess.Popen] = []
    try:
        print(f"[backend] {' '.join(backend_cmd)}", flush=True)
        processes.append(
            subprocess.Popen(
                backend_cmd,
                cwd=str(ROOT),
                env=backend_env,
            )
        )

        print(f"[frontend] {' '.join(frontend_cmd)}", flush=True)
        processes.append(
            subprocess.Popen(
                frontend_cmd,
                cwd=str(FRONTEND_DIR),
                env=frontend_env,
            )
        )

        print()
        print(f"Backend:  {frontend_backend_url}")
        print(f"Frontend: http://{args.frontend_host}:{args.frontend_port}")
        print("Ctrl+C per fermare entrambi i servizi.")

        deadline = time.monotonic() + args.ready_timeout
        backend_ready = False
        frontend_ready = False
        while time.monotonic() < deadline and not (backend_ready and frontend_ready):
            if not backend_ready:
                backend_ready = _wait_for_http(f"{frontend_backend_url}/api/", 1)
            if not frontend_ready:
                frontend_ready = _wait_for_http(f"http://{args.frontend_host}:{args.frontend_port}", 1)

        if backend_ready and frontend_ready and not args.no_open_browser:
            webbrowser.open(f"http://{args.frontend_host}:{args.frontend_port}")
        elif not backend_ready or not frontend_ready:
            print("Avviso: almeno un servizio non ha risposto entro il timeout di avvio.")
        while True:
            for index, process in enumerate(processes):
                code = process.poll()
                if code is not None:
                    raise SystemExit(code)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArresto servizi...", flush=True)
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except Exception:
                if process.poll() is None:
                    process.kill()
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
