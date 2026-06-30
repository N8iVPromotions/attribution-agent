"""
voice_handler.py — Transcribes Telegram voice messages via OpenAI Whisper.
Requires OPENAI_API_KEY in environment. Returns None if not configured.
"""

import os
import tempfile

import requests


def transcribe_voice(file_id: str, bot_token: str) -> str | None:
    """Download a Telegram voice file and transcribe it with Whisper."""
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return None

    # 1. Get Telegram file path
    info = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getFile",
        params={"file_id": file_id},
        timeout=10,
    ).json()
    file_path = info.get("result", {}).get("file_path")
    if not file_path:
        return None

    # 2. Download OGG audio
    audio = requests.get(
        f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
        timeout=30,
    ).content

    # 3. Transcribe via Whisper
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(audio)
        tmp_path = f.name

    try:
        with open(tmp_path, "rb") as af:
            resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {openai_key}"},
                files={"file": ("voice.ogg", af, "audio/ogg")},
                data={"model": "whisper-1"},
                timeout=30,
            )
        return resp.json().get("text", "").strip() or None
    finally:
        os.unlink(tmp_path)
