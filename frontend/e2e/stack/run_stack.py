"""Starts everything the tests need, on ports that can't touch development services.

    python frontend/e2e/stack/run_stack.py

PostgreSQL 5434 and Redis 6380 come from api_tests/docker-compose.test.yml, which is started separately.
Runs in the foreground until stopped. Playwright launches this for you. See frontend/README.md.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend"
LOGS = FRONTEND / "e2e" / ".stack"
API_PORT = os.environ.get("E2E_API_PORT", "8766")
GROQ_PORT = os.environ.get("E2E_GROQ_PORT", "8767")
WEB_PORT = os.environ.get("E2E_WEB_PORT", "4173")
LIVE_KEY = os.environ.get("GROQ_API_KEY", "") if os.environ.get("E2E_LIVE_GROQ") == "1" else ""

env = {
    **os.environ,
    "TEST_DATABASE_URL": os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://rag_test:rag_test@127.0.0.1:5434/rag_api_test"),
    "CELERY_BROKER_URL": "redis://127.0.0.1:6380/0",
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "e2e-access-secret-key-long-enough-for-hs256",
    "JWT_REFRESH_SECRET_KEY": "e2e-refresh-secret-key-long-enough-for-hs256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "DOCUMENT_STORAGE_ROOT": tempfile.mkdtemp(prefix="rag-e2e-"),
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    "PYTHONWARNINGS": "ignore",
    "TRANSFORMERS_VERBOSITY": "error",
    "PYTHONPATH": str(ROOT),
    "API_PROXY_TARGET": f"http://127.0.0.1:{API_PORT}",
}
env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
if LIVE_KEY:
    env.update(GROQ_API_KEY=LIVE_KEY, GROQ_MODEL=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"))
    env.pop("GROQ_BASE_URL", None)
else:
    # Any .env key is overridden, and the SDK reads this address, so real Groq is never called.
    env.update(GROQ_API_KEY="fake-key-for-tests", GROQ_MODEL="fake-groq", GROQ_BASE_URL=f"http://127.0.0.1:{GROQ_PORT}", GROQ_MAX_RETRIES="0")

processes: list[subprocess.Popen] = []


def stop(*_args) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    sys.exit(0)


def start(name: str, command: list[str], cwd: Path = ROOT) -> None:
    log = (LOGS / f"{name}.log").open("w")
    processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT))


def wait_for(url: str, name: str, seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.5)
    print(f"{name} did not start. See {LOGS}.", flush=True)
    stop()


def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    with (LOGS / "seed.log").open("w") as log:
        seeded = subprocess.run([sys.executable, str(FRONTEND / "e2e" / "stack" / "seed.py")], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    if seeded.returncode != 0:
        print(f"Seeding failed. See {LOGS / 'seed.log'}.", flush=True)
        sys.exit(1)

    if not LIVE_KEY:
        start("fake_groq", [sys.executable, str(FRONTEND / "e2e" / "stack" / "fake_groq.py"), GROQ_PORT])
    start("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", API_PORT, "--log-level", "warning"])
    start("worker", [sys.executable, "-m", "celery", "-A", "app.workers.celery_app:celery_app", "worker", "--pool=solo", "--loglevel=warning"])
    wait_for(f"http://127.0.0.1:{API_PORT}/openapi.json", "The API")

    npm = "npm.cmd" if os.name == "nt" else "npm"
    start("web", [npm, "run", "preview", "--", "--port", WEB_PORT, "--strictPort", "--host", "127.0.0.1"], cwd=FRONTEND)
    wait_for(f"http://127.0.0.1:{WEB_PORT}/", "The web server")
    print("Stack ready.", flush=True)

    while all(process.poll() is None for process in processes):
        time.sleep(1)
    print(f"A service stopped unexpectedly. See {LOGS}.", flush=True)
    stop()


if __name__ == "__main__":
    main()
