"""
startup.py — Databricks App entry point
Fetches secrets in the main process (which has Databricks SDK credentials),
injects them as env vars, then starts ARIE and Streamlit as subprocesses.
"""
import base64
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

_SECRET_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ANTHROPIC_API_KEY",
    "GMAIL_SENDER",
    "GMAIL_APP_PASSWORD",
    "OPENAI_API_KEY",
    "META_ACCESS_TOKEN",
    "HUBSPOT_ACCESS_TOKEN",
    "STRIPE_SECRET_KEY",
)


def _fetch_secrets() -> dict:
    """Read secrets from Databricks secret scope in the main process."""
    result = {}
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        for key in _SECRET_KEYS:
            if os.environ.get(key):
                continue  # already set (e.g. local .env)
            try:
                resp = w.secrets.get_secret(scope="attribution", key=key)
                val = resp.value or ""
                try:
                    val = base64.b64decode(val).decode("utf-8")
                except Exception:
                    pass
                if val:
                    result[key] = val
                    print(f"[startup] Loaded secret: {key}", flush=True)
            except Exception as e:
                print(f"[startup] Could not load {key}: {e}", flush=True)
    except Exception as e:
        print(f"[startup] WorkspaceClient failed: {e}", flush=True)
    return result


def main() -> None:
    # Fetch all secrets here — the main process has Databricks credentials
    secrets = _fetch_secrets()
    env = {**os.environ, **secrets}

    # Start ARIE with secrets baked into env
    arie_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "agents" / "control" / "arie_bot.py")],
        cwd=str(ROOT),
        env=env,
    )
    print(f"[startup] ARIE started (pid={arie_proc.pid})", flush=True)

    # Start Streamlit with secrets baked into env
    streamlit_proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.port", "8080",
            "--server.headless", "true",
        ],
        cwd=str(ROOT),
        env=env,
    )
    print(f"[startup] Streamlit started (pid={streamlit_proc.pid})", flush=True)

    def _shutdown(signum, frame):
        print("[startup] Shutting down...", flush=True)
        arie_proc.terminate()
        streamlit_proc.terminate()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        streamlit_proc.wait()
    finally:
        if arie_proc.poll() is None:
            arie_proc.terminate()


if __name__ == "__main__":
    main()
