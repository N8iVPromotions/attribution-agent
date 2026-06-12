"""
utils/guardrails.py
-------------------
Hard guardrails applied to all agent inputs and outputs.

- Blocks prompt injection patterns
- Masks PII before sending to agents
- Validates JSON shape for JSON-contract agents
- Flags governance critical_issues for human approval
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Prompt injection detection patterns
_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|prior|above) instructions", re.I),
    re.compile(r"disregard (the |your )?(system|previous) (prompt|instructions)", re.I),
    re.compile(r"you are now (a |an )?(different|new|other)", re.I),
    re.compile(r"jailbreak|DAN mode|developer mode", re.I),
    re.compile(r"<\|.*?\|>"),          # token-fence style
    re.compile(r"\[\[SYSTEM\]\]", re.I),
]

# JSON-contract agents — their output MUST be valid JSON
_JSON_CONTRACT_AGENTS = {"data-quality", "executive-reporting", "governance-reviewer"}

_MAX_INPUT_CHARS = 50_000


@dataclass
class GuardrailResult:
    blocked: bool = False
    requires_approval: bool = False
    pii_masked: bool = False
    pii_count: int = 0
    warnings: list[str] = field(default_factory=list)
    sanitized_text: str = ""
    block_reason: str = ""


def check_input(message: str, agent_name: str) -> GuardrailResult:
    """Validate and sanitize an agent input message."""
    result = GuardrailResult(sanitized_text=message)

    # Length guard
    if len(message) > _MAX_INPUT_CHARS:
        result.blocked = True
        result.block_reason = f"Input too long: {len(message)} chars (max {_MAX_INPUT_CHARS})"
        logger.warning(f"[Guardrails] BLOCKED {agent_name}: {result.block_reason}")
        return result

    # Prompt injection detection
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(message):
            result.blocked = True
            result.block_reason = f"Prompt injection pattern detected: {pattern.pattern}"
            logger.warning(f"[Guardrails] BLOCKED {agent_name}: {result.block_reason}")
            _log_block(agent_name, result.block_reason)
            return result

    # PII masking
    try:
        from utils.pii_masker import PIIMasker
        masked, report = PIIMasker().mask(message)
        if report.total > 0:
            result.pii_masked = True
            result.pii_count = report.total
            result.sanitized_text = masked
            result.warnings.append(f"Masked {report.total} PII item(s) before agent call")
        else:
            result.sanitized_text = message
    except Exception:
        result.sanitized_text = message

    return result


def check_output(text: str, agent_name: str) -> GuardrailResult:
    """Validate an agent output. For JSON agents, parse and check shape."""
    result = GuardrailResult(sanitized_text=text)

    if agent_name in _JSON_CONTRACT_AGENTS:
        try:
            import json
            cleaned = text.strip()
            if cleaned.startswith("```"):
                parts = cleaned.split("```")
                cleaned = parts[1].lstrip("json").strip() if len(parts) > 1 else cleaned
            json.loads(cleaned)
        except Exception as exc:
            result.warnings.append(f"Output is not valid JSON: {exc}")
            logger.warning(f"[Guardrails] {agent_name} output JSON invalid: {exc}")

    # Flag governance critical_issues for human approval
    if agent_name == "governance-reviewer":
        try:
            import json
            parsed = json.loads(text)
            if parsed.get("critical_issues"):
                result.requires_approval = True
                result.warnings.append(
                    f"Governance critical issues flagged: {parsed['critical_issues']}"
                )
        except Exception:
            pass

    return result


def _log_block(agent_name: str, reason: str) -> None:
    try:
        from utils.audit_logger import log_event
        log_event(
            "GUARDRAIL_BLOCK",
            actor="system",
            resource=agent_name,
            action="input_check",
            outcome="blocked",
            detail={"reason": reason},
        )
    except Exception:
        pass
