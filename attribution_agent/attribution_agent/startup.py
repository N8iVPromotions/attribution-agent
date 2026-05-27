"""
startup.py — Databricks App entry point
Starts the ARIE Telegram bot as a background process, then Streamlit.
Keeps both alive; shuts both down cleanly on SIGTERM/SIGINT.
"""
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main() -> None:
    env = {**os.environ}

    arie_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "agents" / "control" / "arie_bot.py")],
        cwd=str(ROOT),
        env=env,
    )
    print(f"[startup] ARIE started (pid={arie_proc.pid})", flush=True)

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
