from __future__ import annotations

import json

from utils.sql import sql_literal


def _decode_databricks_string_literal(literal: str) -> str:
    """Model Databricks' documented backslash preprocessing for a test round trip."""
    assert literal.startswith("'") and literal.endswith("'")
    value = literal[1:-1].replace("''", "'")
    escapes = {
        "0": "\0",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "Z": "\x1a",
    }
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            decoded.append(escapes.get(value[index + 1], value[index + 1]))
            index += 2
            continue
        decoded.append(value[index])
        index += 1
    return "".join(decoded)


def test_sql_literal_preserves_json_escapes_through_databricks_parser():
    payload = {
        "error": 'Stripe rejected {"error": "missing Accounts Read"}',
        "path": r"C:\reports\closed-won",
    }
    encoded = json.dumps(payload)

    stored = _decode_databricks_string_literal(sql_literal(encoded))

    assert json.loads(stored) == payload


def test_sql_literal_keeps_quotes_safe_after_a_backslash():
    value = "\\'; DROP TABLE revenue; --"

    literal = sql_literal(value)

    assert _decode_databricks_string_literal(literal) == value
