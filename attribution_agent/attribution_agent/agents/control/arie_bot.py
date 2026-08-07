"""
arie_bot.py — Automatic Revenue Intelligence Engine (ARIE)
-----------------------------------------------------------
Telegram bot for voice/text control of the attribution pipeline.
All destructive actions require your explicit approval via inline buttons.

Start via app.py (auto-starts as a background thread) or standalone:
    python agents/control/arie_bot.py
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_BASE = "https://api.telegram.org/bot{token}/{method}"

# ── Approval queue ─────────────────────────────────────────────
_lock = threading.Lock()
_pending: dict[str, dict] = {}  # action_id → {description, callback}

# ── Bot singleton state ────────────────────────────────────────
_bot_token: str = ""
_chat_id: str = ""
_running: bool = False
_thread: threading.Thread | None = None
_bot_lock_handle = None


def _bot_lock_path() -> Path:
    configured = os.environ.get("ARIE_BOT_LOCK_FILE", "")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "arie_bot.lock"


def _acquire_bot_lock() -> bool:
    """Prevent duplicate long-polling listeners for the same Telegram token."""
    global _bot_lock_handle
    if _bot_lock_handle:
        return True

    path = _bot_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _bot_lock_handle = handle
    return True


def _release_bot_lock() -> None:
    global _bot_lock_handle
    if not _bot_lock_handle:
        return
    try:
        if os.name == "nt":
            import msvcrt

            _bot_lock_handle.seek(0)
            msvcrt.locking(_bot_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_bot_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        _bot_lock_handle.close()
        _bot_lock_handle = None


def _json_dumps(value: object) -> str:
    try:
        return json.dumps(value or {}, sort_keys=True)
    except Exception:
        return "{}"


def _log_telegram_event(record: dict) -> None:
    """Best-effort durable event log for inbound Telegram messages/callbacks."""
    try:
        from utils.databricks_writer import write_telegram_event

        write_telegram_event(record)
    except Exception as exc:
        logger.debug(f"[ARIE] telegram_events write failed: {exc}")


def _message_event_base(message: dict, update_id: int | None) -> dict:
    sender = message.get("from", {}) or {}
    chat = message.get("chat", {}) or {}
    voice = message.get("voice", {}) or {}
    message_id = message.get("message_id")
    event_id = (
        f"telegram-message-{update_id or 'no-update'}-"
        f"{message_id or uuid.uuid4().hex[:8]}"
    )
    return {
        "event_id": event_id,
        "event_time": datetime.now(timezone.utc),
        "update_id": update_id,
        "message_id": message_id,
        "chat_id": chat.get("id", ""),
        "user_id": sender.get("id", ""),
        "username": sender.get("username", ""),
        "event_type": "message",
        "raw_text": message.get("text", "") or "",
        "voice_file_id": voice.get("file_id", ""),
        "parsed_intent": "",
        "params_json": "{}",
        "action_id": "",
        "response_summary": "",
        "status": "received",
        "error": "",
    }


def _callback_event_base(query: dict, update_id: int | None) -> dict:
    sender = query.get("from", {}) or {}
    message = query.get("message", {}) or {}
    chat = message.get("chat", {}) or {}
    callback_id = query.get("id", "") or uuid.uuid4().hex[:8]
    return {
        "event_id": f"telegram-callback-{update_id or 'no-update'}-{callback_id}",
        "event_time": datetime.now(timezone.utc),
        "update_id": update_id,
        "message_id": message.get("message_id"),
        "chat_id": chat.get("id", ""),
        "user_id": sender.get("id", ""),
        "username": sender.get("username", ""),
        "event_type": "callback_query",
        "raw_text": query.get("data", "") or "",
        "voice_file_id": "",
        "parsed_intent": "approval_callback",
        "params_json": "{}",
        "action_id": "",
        "response_summary": "",
        "status": "received",
        "error": "",
    }


# ── Telegram helpers ───────────────────────────────────────────
def _api(method: str, **params) -> dict:
    url = _TELEGRAM_BASE.format(token=_bot_token, method=method)
    try:
        resp = requests.post(url, json=params, timeout=30)
        return resp.json()
    except Exception as exc:
        logger.error(f"ARIE Telegram error: {exc}")
        return {}


def send_message(text: str, reply_markup: dict | None = None) -> dict:
    params: dict = {"chat_id": _chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        params["reply_markup"] = reply_markup
    return _api("sendMessage", **params)


def notify(text: str) -> None:
    """Send a Telegram message from anywhere — app thread or standalone Job."""
    if is_running():
        send_message(text)
        return
    # Fallback: read credentials and fire directly (works in Databricks Jobs
    # where the bot thread is not running).
    token, chat_id = _load_credentials()
    if token and chat_id:
        try:
            requests.post(
                _TELEGRAM_BASE.format(token=token, method="sendMessage"),
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
        except Exception as exc:
            logger.error(f"ARIE notify failed: {exc}")


def _load_credentials() -> tuple[str, str]:
    """Read TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from the environment."""
    return os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    )


def _write_approval_queue(action_id: str, description: str, action_type: str) -> None:
    """Persist approval request to Delta for API visibility. Best-effort — never raises."""
    try:
        import pandas as pd
        from datetime import datetime, timezone
        from utils.databricks_writer import _upsert_dataframe

        _OPS = __import__("os").environ.get(
            "ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops"
        )
        df = pd.DataFrame(
            [
                {
                    "action_id": action_id,
                    "created_at": datetime.now(timezone.utc),
                    "actor": "arie_bot",
                    "description": description,
                    "action_type": action_type,
                    "payload_json": "{}",
                    "status": "pending",
                    "resolved_at": None,
                    "resolved_by": "",
                    "resolution_note": "",
                    "channel": "telegram",
                }
            ]
        )
        _upsert_dataframe(df, _OPS, "approval_queue", ["action_id"])
    except Exception as exc:
        logger.debug(f"[ARIE] approval_queue write failed: {exc}")


def _update_approval_queue(action_id: str, status: str) -> None:
    """Update the resolution status in Delta. Best-effort — never raises."""
    try:
        from datetime import datetime, timezone
        from utils.databricks_writer import _run_sql

        _OPS = __import__("os").environ.get(
            "ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops"
        )
        now = datetime.now(timezone.utc).isoformat()
        _run_sql(
            f"UPDATE {_OPS}.approval_queue "
            f"SET status = '{status}', resolved_at = CAST('{now}' AS TIMESTAMP), resolved_by = 'telegram' "
            f"WHERE action_id = '{action_id}'"
        )
    except Exception as exc:
        logger.debug(f"[ARIE] approval_queue update failed: {exc}")


def request_approval(description: str, callback, action_type: str = "generic") -> str:
    """Queue an action and send an Approve/Reject prompt. Returns action_id."""
    action_id = uuid.uuid4().hex[:8]
    with _lock:
        _pending[action_id] = {"description": description, "callback": callback}

    _write_approval_queue(action_id, description, action_type)

    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{action_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{action_id}"},
            ]
        ]
    }
    send_message(
        f"*Action pending your approval*\n\n{description}", reply_markup=markup
    )
    return action_id


# ── Intent parsing via Claude ──────────────────────────────────
def _parse_intent(text: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"intent": "unknown", "params": {}}

    prompt = f"""You are ARIE, the Automatic Revenue Intelligence Engine for N8iV PROMOTIONS.
Parse this command and return ONLY valid JSON — no markdown, no explanation.

Command: "{text}"

Valid intents:
- run_pipeline     (run attribution pipeline for a client)
- generate_outreach (generate email sequence for a prospect)
- check_status     (show system/pipeline status)
- list_prospects   (list outreach prospects)
- list_clients     (list attribution clients)
- help             (show available commands)
- unknown          (anything else)

Return exactly:
{{
  "intent": "<intent>",
  "params": {{
    "client_id": "<client id if mentioned, else null>",
    "prospect_name": "<prospect business name if mentioned, else null>",
    "dry_run": <true if test/dry/preview/check mentioned, false otherwise>
  }}
}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        raw = resp.json()["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception:
        return {"intent": "unknown", "params": {}}


# ── Callback query handler (inline button presses) ─────────────
def _handle_callback_query(query: dict, update_id: int | None = None) -> None:
    event_record = _callback_event_base(query, update_id)
    data = query.get("data", "")
    qid = query["id"]

    if ":" not in data:
        event_record.update(
            {
                "status": "ignored",
                "response_summary": "Ignored callback without action prefix",
            }
        )
        _log_telegram_event(event_record)
        return

    action, action_id = data.split(":", 1)
    event_record["action_id"] = action_id
    event_record["params_json"] = _json_dumps(
        {"action": action, "action_id": action_id}
    )

    with _lock:
        pending = _pending.pop(action_id, None)

    if not pending:
        _api("answerCallbackQuery", callback_query_id=qid, text="Already processed.")
        event_record.update(
            {
                "status": "already_processed",
                "response_summary": "Callback action was already processed",
            }
        )
        _log_telegram_event(event_record)
        return

    if action == "approve":
        _api("answerCallbackQuery", callback_query_id=qid, text="✅ Approved")
        send_message("✅ *Approved* — executing now...")
        _update_approval_queue(action_id, "approved")
        try:
            result = pending["callback"]()
            event_record.update(
                {
                    "status": "approved",
                    "response_summary": str(result)[:1000],
                }
            )
            send_message(f"✓ *Done*\n\n{result}")
        except Exception as exc:
            event_record.update(
                {
                    "status": "failed",
                    "response_summary": "Callback execution failed",
                    "error": str(exc)[:1000],
                }
            )
            send_message(f"⚠️ *Failed*\n\n{exc}")
    else:
        _api("answerCallbackQuery", callback_query_id=qid, text="❌ Rejected")
        send_message("❌ *Rejected* — action cancelled.")
        _update_approval_queue(action_id, "rejected")
        event_record.update(
            {
                "status": "rejected",
                "response_summary": "Action rejected in Telegram",
            }
        )

    _log_telegram_event(event_record)


# ── Message handler ────────────────────────────────────────────
def _handle_message(message: dict, update_id: int | None = None) -> None:
    event_record = _message_event_base(message, update_id)
    text = message.get("text", "") or ""
    voice = message.get("voice")

    # Transcribe voice if present
    if voice and not text:
        from agents.control.voice_handler import transcribe_voice

        text = transcribe_voice(voice["file_id"], _bot_token) or ""
        if text:
            send_message(f'🎙️ _Heard: "{text}"_')
        else:
            send_message(
                "⚠️ Voice transcription not configured.\n"
                "Add `OPENAI_API_KEY` to your `.env` to enable voice commands.\n"
                'Text commands work now — say *"help"* to get started.'
            )
            event_record.update(
                {
                    "status": "failed",
                    "response_summary": "Voice transcription not configured",
                    "error": "No transcription text returned",
                }
            )
            _log_telegram_event(event_record)
            return

    text = text.strip()
    if not text:
        event_record.update(
            {
                "status": "ignored",
                "response_summary": "Ignored empty Telegram message",
            }
        )
        _log_telegram_event(event_record)
        return

    parsed = _parse_intent(text)
    intent = parsed.get("intent", "unknown")
    params = parsed.get("params", {})
    event_record["raw_text"] = text
    event_record["parsed_intent"] = intent
    event_record["params_json"] = _json_dumps(params)
    response_summary = ""
    status = "handled"
    action_id = ""

    # ── STATUS ──────────────────────────────────────────────────
    if intent == "check_status":
        from config.client_config import CLIENT_REGISTRY
        from agents.outreach.outreach_agent import PROSPECTS

        send_message(
            f"*ARIE Status* 🟢 Online\n\n"
            f"📊 *Clients:* {', '.join(CLIENT_REGISTRY.keys())}\n"
            f"🎯 *Prospects:* {len(PROSPECTS)}\n"
            f"⏳ *Pending approvals:* {len(_pending)}"
        )
        response_summary = "Sent status overview"

    # ── LIST PROSPECTS ──────────────────────────────────────────
    elif intent == "list_prospects":
        from agents.outreach.outreach_agent import PROSPECTS

        lines = [f"{p['id']}. *{p['name']}* — {p['industry']}" for p in PROSPECTS]
        send_message("*Outreach Prospects*\n\n" + "\n".join(lines))
        response_summary = f"Listed {len(PROSPECTS)} prospects"

    # ── LIST CLIENTS ────────────────────────────────────────────
    elif intent == "list_clients":
        from config.client_config import CLIENT_REGISTRY

        lines = [f"• `{cid}`" for cid in CLIENT_REGISTRY]
        send_message("*Attribution Clients*\n\n" + "\n".join(lines))
        response_summary = f"Listed {len(CLIENT_REGISTRY)} clients"

    # ── GENERATE OUTREACH ───────────────────────────────────────
    elif intent == "generate_outreach":
        from agents.outreach.outreach_agent import PROSPECTS, generate_email_sequence

        name = (params.get("prospect_name") or "").lower()
        matches = [p for p in PROSPECTS if name in p["name"].lower()] if name else []

        if not matches:
            lines = [f"{p['id']}. {p['name']}" for p in PROSPECTS]
            send_message(
                "Which prospect?\n\n"
                + "\n".join(lines)
                + '\n\nSay _"generate emails for [business name]"_'
            )
            event_record.update(
                {
                    "status": "needs_clarification",
                    "response_summary": "Asked operator to choose a prospect",
                }
            )
            _log_telegram_event(event_record)
            return

        prospect = matches[0]

        def do_generate():
            seq = generate_email_sequence(prospect)
            subjects = "\n".join(
                f"  {i + 1}. _{seq[f'email{i + 1}']['subject']}_" for i in range(3)
            )
            return f"3 emails ready for *{prospect['name']}*\n\n{subjects}"

        action_id = request_approval(
            f"Generate 3-email sequence for *{prospect['name']}* ({prospect['industry']})?",
            do_generate,
        )
        status = "approval_requested"
        response_summary = f"Requested approval for outreach to {prospect['name']}"

    # ── RUN PIPELINE ────────────────────────────────────────────
    elif intent == "run_pipeline":
        client_id = params.get("client_id") or "n8iv_promotions"
        dry_run = bool(params.get("dry_run", True))
        label = "dry run" if dry_run else "live run"
        warning = (
            "_Dry run — no emails will be sent._"
            if dry_run
            else "⚠️ _Live run — reports will be emailed to clients._"
        )

        def do_run():
            # Remote Jobs-API trigger is Databricks-only. Off-Databricks the SDK
            # would still authenticate (DATABRICKS_HOST/TOKEN power the SQL
            # connector) and fire the old job — require an explicit opt-in.
            if not os.environ.get("ATTRIBUTION_JOBS_API_ENABLED"):
                return (
                    "Remote pipeline trigger is not available in this deployment — "
                    "use the dashboard's Run button."
                )

            import threading
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.jobs import RunLifeCycleState

            job_id = int(os.environ.get("ATTRIBUTION_JOB_ID", "500226442246561"))
            run_params = ["--agency", "n8iv_promotions", "--client-filter", client_id]
            if dry_run:
                run_params.append("--dry-run")

            w = WorkspaceClient()
            run = w.jobs.run_now(job_id=job_id, python_params=run_params)
            run_id = run.run_id

            def _poll():
                import time

                terminal = {
                    RunLifeCycleState.TERMINATED,
                    RunLifeCycleState.SKIPPED,
                    RunLifeCycleState.INTERNAL_ERROR,
                }
                while True:
                    time.sleep(30)
                    try:
                        info = w.jobs.get_run(run_id=run_id)
                        if info.state.life_cycle_state in terminal:
                            result_state = info.state.result_state
                            if result_state and result_state.value == "SUCCESS":
                                send_message(
                                    f"✅ *Pipeline {label} complete*\n"
                                    f"Client: `{client_id}` · Run: `{run_id}`"
                                )
                            else:
                                state_val = (
                                    result_state.value if result_state else "unknown"
                                )
                                send_message(
                                    f"❌ *Pipeline {label} failed* — `{state_val}`\n"
                                    f"Run: `{run_id}`"
                                )
                            break
                    except Exception as exc:
                        send_message(f"⚠️ Could not poll run status: `{exc}`")
                        break

            threading.Thread(
                target=_poll, daemon=True, name=f"arie-poll-{run_id}"
            ).start()
            return f"Submitted · Run ID: `{run_id}`\nI'll message you when it's done."

        action_id = request_approval(
            f"Run attribution pipeline (*{label}*) for `{client_id}`?\n\n{warning}",
            do_run,
        )
        status = "approval_requested"
        response_summary = f"Requested approval for pipeline {label} for {client_id}"

    # ── HELP ────────────────────────────────────────────────────
    elif intent == "help":
        send_message(
            "*ARIE — Automatic Revenue Intelligence Engine* 🤖\n\n"
            "Here's what you can tell me:\n\n"
            "📊 *Status & overview*\n"
            '  _"What\'s the status?"_\n'
            '  _"List all prospects"_\n'
            '  _"List active clients"_\n\n'
            "✉️ *Outreach*\n"
            '  _"Generate emails for Alani Skin MD"_\n'
            '  _"Create outreach for Dolce Medical Spa"_\n\n'
            "⚡ *Pipeline*\n"
            '  _"Run pipeline for N8iV Promotions (dry run)"_\n'
            '  _"Run live pipeline"_\n\n'
            "🎙️ Voice commands work too — just send a voice message!"
        )
        response_summary = "Sent help menu"

    # ── UNKNOWN ─────────────────────────────────────────────────
    else:
        send_message('I didn\'t catch that. Say *"help"* to see what I can do.')
        status = "unknown"
        response_summary = "Sent fallback help prompt"

    event_record.update(
        {
            "action_id": action_id,
            "response_summary": response_summary,
            "status": status,
        }
    )
    _log_telegram_event(event_record)


# ── Long-polling loop ──────────────────────────────────────────
def _poll_loop() -> None:
    global _running
    offset = _initial_update_offset()
    startup_response = send_message(
        "🟢 *ARIE online*\n"
        "Automatic Revenue Intelligence Engine ready.\n\n"
        'Say *"help"* to see available commands.'
    )
    startup_message = (startup_response or {}).get("result", {}) or {}
    _log_telegram_event(
        {
            "event_id": f"telegram-startup-{uuid.uuid4().hex[:12]}",
            "event_time": datetime.now(timezone.utc),
            "message_id": startup_message.get("message_id"),
            "chat_id": _chat_id,
            "event_type": "bot_status",
            "raw_text": "ARIE online",
            "parsed_intent": "startup",
            "params_json": "{}",
            "response_summary": "Startup notification sent",
            "status": "online",
        }
    )

    while _running:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{_bot_token}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=30,
            )
            updates = resp.json().get("result", [])
        except Exception:
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                if "message" in update:
                    chat_id = str(update["message"].get("chat", {}).get("id", ""))
                    if chat_id == str(_chat_id):
                        _handle_message(
                            update["message"], update_id=update.get("update_id")
                        )
                elif "callback_query" in update:
                    cq = update["callback_query"]
                    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                    if chat_id == str(_chat_id):
                        _handle_callback_query(cq, update_id=update.get("update_id"))
            except Exception as exc:
                logger.exception(f"ARIE update error: {exc}")


# ── Public API ─────────────────────────────────────────────────
def start(token: str, chat_id: str) -> None:
    global _bot_token, _chat_id, _running, _thread
    if _thread and _thread.is_alive():
        return
    if not _acquire_bot_lock():
        raise RuntimeError(
            "Another ARIE Telegram listener is already running. Stop it before starting a new one."
        )
    _bot_token = token
    _chat_id = str(chat_id)
    _running = True
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="ARIE")
    _thread.start()
    logger.info("ARIE started")


def stop() -> None:
    global _running
    _running = False
    _release_bot_lock()


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def _initial_update_offset() -> int:
    """Start after already-pending updates so restarts do not replay old commands."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{_bot_token}/getUpdates",
            params={
                "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=10,
        )
        updates = resp.json().get("result", [])
        if not updates:
            return 0
        return max(update["update_id"] for update in updates) + 1
    except Exception:
        return 0


# ── Standalone entry point ─────────────────────────────────────
if __name__ == "__main__":
    import sys

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    _token, _chat_id_env = _load_credentials()

    if not _token or not _chat_id_env:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")
        sys.exit(1)

    start(_token, _chat_id_env)
    print("ARIE running — press Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
