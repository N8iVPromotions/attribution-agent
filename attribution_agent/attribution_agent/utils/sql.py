from __future__ import annotations


def sql_literal(value: object) -> str:
    """Return a safely escaped SQL string literal."""
    escaped = str(value or "").replace("\\", "\\\\").replace("'", "''")
    return "'" + escaped + "'"
