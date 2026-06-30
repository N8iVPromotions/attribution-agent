"""
tests/test_model_gateway.py
---------------------------
Covers the forced structured-output path added to model_gateway.call():
when a response_schema is supplied, the gateway must force tool-use and return
schema-valid JSON as text. Without a schema it returns the plain text block.
"""
import json
import sys
import types

# Stub the anthropic SDK if it isn't installed in this env, so importing the
# gateway never fails at collection time. Must run before importing the gateway.
if "anthropic" not in sys.modules:
    try:  # pragma: no cover - depends on env
        import anthropic  # noqa: F401
    except ImportError:
        sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=object)

from utils import model_gateway as gw
from agents.intelligence import n8iv_agents as n8


class _Usage:
    input_tokens = 10
    output_tokens = 20
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Block:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content):
        self.content = content
        self.usage = _Usage()


def _fake_client_factory(calls, tool_input):
    """Returns a fake Anthropic class whose create() records kwargs and emits a
    tool_use block when tools are present, else a plain text block."""
    class _Messages:
        def create(self, **kw):
            calls.append(kw)
            if "tools" in kw:
                return _Resp([_Block("tool_use", name=kw["tools"][0]["name"], input=tool_input)])
            return _Resp([_Block("text", text='{"narrative": "plain"}')])

    class _Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    return _Anthropic


def test_response_schema_forces_tool_use_and_returns_valid_json(monkeypatch):
    calls: list[dict] = []
    tool_input = {
        "narrative": "Solid month.",
        "key_findings": ["Meta led pipeline"],
        "top_channel": "Meta",
        "total_pipeline": 120000.0,
        "total_spend": 30000.0,
        "overall_roi": 4.0,
        "collected_revenue": 90000.0,
        "refund_rate": 0.0,
        "true_roi": 3.0,
        "attribution_model": "last_touch",
    }
    monkeypatch.setattr(gw.anthropic, "Anthropic", _fake_client_factory(calls, tool_input))

    resp = gw.call(
        agent_name="executive-reporting",
        system_prompt="sys",
        user_message="msg",
        task_type="executive_reporting",  # not cacheable -> no DB cache branch
        response_schema=n8.EXEC_REPORT_SCHEMA,
    )

    sent = calls[-1]
    assert sent["tool_choice"] == {"type": "tool", "name": "emit_structured_report"}
    assert sent["tools"][0]["input_schema"] is n8.EXEC_REPORT_SCHEMA
    # Returned text is always valid JSON the callers can json.loads() safely.
    assert json.loads(resp.text) == tool_input


def test_cache_key_distinguishes_long_messages_with_shared_prefix():
    # Cacheable-task messages share a long fixed prefix; the key must reflect the
    # full message, not a truncated head, or distinct inputs collide.
    prefix = "Client: acme\n\nIngest summary: " + ("x" * 400) + "\n\nFindings:\n"
    key_a = gw._cache_key("claude-haiku", "data-quality", prefix + "- duplicate emails")
    key_b = gw._cache_key("claude-haiku", "data-quality", prefix + "- null revenue")
    assert key_a != key_b
    # Stable for identical input, and still a 32-char hex digest.
    assert gw._cache_key("claude-haiku", "data-quality", prefix) == gw._cache_key(
        "claude-haiku", "data-quality", prefix
    )
    assert len(key_a) == 32


def test_no_schema_returns_plain_text_block(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(gw.anthropic, "Anthropic", _fake_client_factory(calls, {}))

    resp = gw.call(
        agent_name="revenue-analyst",
        system_prompt="sys",
        user_message="msg",
        task_type="revenue_analyst",
    )

    assert "tools" not in calls[-1]
    assert resp.text == '{"narrative": "plain"}'
