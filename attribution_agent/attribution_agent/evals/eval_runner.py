"""
evals/eval_runner.py
---------------------
CLI for running behavioral regression evals against golden datasets.

Usage:
    python evals/eval_runner.py --agent revenue-analyst
    python evals/eval_runner.py --all-agents --ci-mode

Exits with code 1 if any agent scores below 0.80 (regression).
In CI mode, also exits 1 if score is worse than the most recent prior run.
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")

_ALL_AGENTS = [
    "data-quality",
    "revenue-analyst",
    "executive-reporting",
    "governance-reviewer",
]


def _object_schema(properties: dict[str, dict]) -> dict:
    """Build the strict tool schema used by the production-like eval calls."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_EVAL_RESPONSE_SCHEMAS = {
    "data-quality": _object_schema(
        {
            "status": {"type": "string", "enum": ["complete", "partial", "failed"]},
            "failed_source_count": {"type": "integer"},
            "total_rows": {"type": "integer"},
            "missing_campaign_ids": {"type": "integer"},
            "duplicate_records": {"type": "integer"},
            "delivery_allowed": {"type": "boolean"},
        }
    ),
    "revenue-analyst": _object_schema(
        {
            "report_month": {"type": "string"},
            "total_spend": {"type": "number"},
            "attributed_pipeline": {"type": "number"},
            "collected_revenue": {"type": "number"},
            "top_channel": {"type": "string"},
            "top_channel_pipeline": {"type": "number"},
            "failed_source_count": {"type": "integer"},
            "attribution_is_causal": {"type": "boolean"},
        }
    ),
    "executive-reporting": _object_schema(
        {
            "status": {
                "type": "string",
                "enum": [
                    "DRAFT_HUMAN_REVIEW_REQUIRED",
                    "REVISE_BEFORE_HUMAN_REVIEW",
                ],
                "description": "Machine-readable workflow status, not a display label.",
            },
            "report_month": {"type": "string"},
            "overall_confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "recommended_action": {"type": "string"},
            "total_spend": {"type": "number"},
            "attributed_pipeline": {"type": "number"},
            "collected_revenue": {"type": "number"},
            "top_channel": {"type": "string"},
            "delivery_allowed": {"type": "boolean"},
            "human_approval_required": {"type": "boolean"},
        }
    ),
    "governance-reviewer": _object_schema(
        {
            "decision": {
                "type": "string",
                "enum": [
                    "READY_FOR_HUMAN_REVIEW",
                    "REVISE_BEFORE_HUMAN_REVIEW",
                    "BLOCKED_ESCALATION_REQUIRED",
                ],
            },
            "risk_tier": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
            },
            "privacy_violation": {"type": "boolean"},
            "causal_claim_violation": {"type": "boolean"},
            "partial_data_disclosed": {
                "type": "boolean",
                "description": (
                    "Whether the quoted client-facing draft itself discloses the "
                    "partial-data limitation. Reviewer-only evidence does not count."
                ),
            },
            "human_approval_required": {"type": "boolean"},
            "critical_issue_count": {"type": "integer"},
        }
    ),
}


def _get_last_score(agent_name: str) -> float | None:
    try:
        from utils.databricks_writer import _get_connection, _is_databricks, _get_spark

        query = (
            f"SELECT score FROM {_OPS_SCHEMA}.eval_results "
            f"WHERE agent_name = '{agent_name}' "
            f"ORDER BY run_at DESC LIMIT 1"
        )
        if _is_databricks():
            rows = _get_spark().sql(query).collect()
            return float(rows[0]["score"]) if rows else None
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        return None


def _write_result(
    agent_name: str,
    sample_id: str,
    passed: bool,
    score: float,
    field_results: dict,
    regression: bool,
    notes: str,
) -> None:
    try:
        import pandas as pd
        from utils.databricks_writer import _upsert_dataframe

        df = pd.DataFrame(
            [
                {
                    "eval_id": uuid.uuid4().hex,
                    "run_at": datetime.now(timezone.utc),
                    "agent_name": agent_name,
                    "prompt_version": os.environ.get("GIT_PROMPT_TAG", "dev"),
                    "model_id": os.environ.get("EVAL_MODEL_ID", "claude-sonnet-4-6"),
                    "sample_id": sample_id,
                    "passed": passed,
                    "field_results": json.dumps(field_results),
                    "score": score,
                    "regression": regression,
                    "notes": notes,
                }
            ]
        )
        _upsert_dataframe(df, _OPS_SCHEMA, "eval_results", ["eval_id"])
    except Exception as exc:
        logger.warning(f"[Eval] Could not write eval result: {exc}")


def run_eval_for_agent(
    agent_name: str,
    ci_mode: bool = False,
    require_samples: bool = False,
) -> tuple[bool, float]:
    """
    Run all active golden samples for an agent.
    Returns (passed, avg_score). In CI mode, also checks for regression.
    """
    from evals.golden_dataset import GoldenDatasetManager

    manager = GoldenDatasetManager()
    samples = manager.load_samples(agent_name)

    if not samples:
        level = logger.error if require_samples else logger.info
        level(f"[Eval] No golden samples for {agent_name}")
        return (not require_samples), (0.0 if require_samples else 1.0)

    prior_score = _get_last_score(agent_name) if ci_mode else None
    scores = []
    sample_passes = []
    any_regression = False

    for sample in samples:
        try:
            actual = _call_agent_for_eval(agent_name, sample["input_summary"])
            eval_result = manager.evaluate(agent_name, actual, sample)
            score = eval_result["score"]
            passed = eval_result["passed"]
            if not passed:
                failed_fields = {
                    field: result
                    for field, result in eval_result["field_results"].items()
                    if not result["passed"]
                }
                logger.error(
                    "[Eval] Sample %s failed fields: %s",
                    sample["sample_id"],
                    json.dumps(failed_fields, sort_keys=True, default=str),
                )
            regression = (
                ci_mode and prior_score is not None and score < prior_score - 0.05
            )

            if regression:
                any_regression = True
                logger.error(
                    f"[Eval] REGRESSION for {agent_name}: "
                    f"score={score:.2f} vs prior={prior_score:.2f}"
                )

            _write_result(
                agent_name=agent_name,
                sample_id=sample["sample_id"],
                passed=passed,
                score=score,
                field_results=eval_result["field_results"],
                regression=regression,
                notes="",
            )
            scores.append(score)
            sample_passes.append(passed)
        except Exception as exc:
            logger.warning(f"[Eval] Failed to evaluate {agent_name} sample: {exc}")
            scores.append(0.0)
            sample_passes.append(False)

    avg_score = sum(scores) / len(scores) if scores else 0.0
    overall_passed = all(sample_passes) and avg_score >= 0.80 and not any_regression
    logger.info(
        f"[Eval] {agent_name}: avg_score={avg_score:.2f}, passed={overall_passed}"
    )
    return overall_passed, avg_score


def _call_agent_for_eval(agent_name: str, input_text: str) -> str:
    """Call agent with minimal context for eval purposes."""
    from utils.prompt_loader import PromptLoader
    from utils.model_gateway import call as gw_call

    loader = PromptLoader()
    prompt_def = loader.load(agent_name)
    task_type_map = {
        "data-quality": "data_quality",
        "revenue-analyst": "revenue_analyst",
        "executive-reporting": "executive_reporting",
        "governance-reviewer": "governance_review",
    }
    resp = gw_call(
        agent_name=agent_name,
        system_prompt=prompt_def.body,
        user_message=input_text,
        max_tokens=1500,
        task_type=task_type_map.get(agent_name, ""),
        response_schema=_EVAL_RESPONSE_SCHEMAS.get(agent_name),
    )
    return resp.text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=str, help="Agent name to evaluate")
    parser.add_argument("--all-agents", action="store_true")
    parser.add_argument("--ci-mode", action="store_true", help="Exit 1 on regression")
    parser.add_argument(
        "--require-samples",
        action="store_true",
        help="Fail when an evaluated agent has no active golden samples",
    )
    parser.add_argument(
        "--seed-file",
        type=str,
        help="Upsert a PII-masked JSON/JSONL seed set before evaluation",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    if args.seed_file:
        from evals.golden_dataset import GoldenDatasetManager

        count = GoldenDatasetManager().seed_file(args.seed_file)
        logger.info("[Eval] Seeded %s golden samples", count)

    agents = _ALL_AGENTS if args.all_agents else ([args.agent] if args.agent else [])
    if not agents:
        parser.error("Provide --agent or --all-agents")

    all_passed = True
    for agent_name in agents:
        passed, score = run_eval_for_agent(
            agent_name,
            ci_mode=args.ci_mode,
            require_samples=args.require_samples,
        )
        if not passed:
            all_passed = False

    if not all_passed:
        logger.error("[Eval] One or more agents failed eval gate")
        sys.exit(1)
    logger.info("[Eval] All agents passed")


if __name__ == "__main__":
    main()
