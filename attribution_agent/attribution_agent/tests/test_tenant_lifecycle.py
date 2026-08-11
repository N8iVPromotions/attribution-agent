from __future__ import annotations

import json

import pytest

from flows.tenant_lifecycle import (
    LifecycleRequest,
    agency_schema,
    business_schema,
    execute_lifecycle,
)


class FakeSql:
    def __init__(
        self,
        *,
        agency: dict | None = None,
        business: dict | None = None,
        active_runs: int = 0,
        active_clients: int = 0,
        prior_operation: dict | None = None,
    ):
        self.agency = agency
        self.business = business
        self.active_runs = active_runs
        self.active_clients = active_clients
        self.prior_operation = prior_operation
        self.statements: list[str] = []

    def __call__(self, statement: str) -> list[dict]:
        self.statements.append(statement)
        if "FROM workspace.attribution_ops.tenant_lifecycle_operations" in statement:
            return [self.prior_operation] if self.prior_operation else []
        if "FROM workspace.attribution_ops.agency_registry" in statement:
            return [self.agency] if self.agency else []
        if "COUNT(*) AS active_clients" in statement:
            return [{"active_clients": self.active_clients}]
        if "FROM workspace.attribution_ops.client_registry" in statement:
            return [self.business] if self.business else []
        if "COUNT(*) AS active_runs" in statement:
            return [{"active_runs": self.active_runs}]
        return []


def request(command: str, entity_id: str, **overrides) -> LifecycleRequest:
    values = {
        "command": command,
        "entity_id": entity_id,
        "entity_name": "Acme Company",
        "agency_id": "acme_agency",
        "report_email": "reports@acme.test",
        "attribution_model": "linear",
        "confirmation": "",
        "request_id": "request-12345678",
        "requested_by": "test-operator",
    }
    values.update(overrides)
    return LifecycleRequest(**values)


def test_schema_names_are_derived_from_validated_ids():
    assert agency_schema("acme_agency") == "workspace.agency_acme_agency"
    assert business_schema("acme_business") == "workspace.attribution_acme_business"


@pytest.mark.parametrize(
    "bad_id",
    ["acme; DROP SCHEMA workspace.attribution_ops", "UPPER", "two words", "../x"],
)
def test_identifiers_reject_sql_injection_and_noncanonical_slugs(bad_id):
    with pytest.raises(ValueError):
        execute_lifecycle(request("create_agency", bad_id), FakeSql())


def test_delete_requires_exact_confirmation():
    with pytest.raises(ValueError, match="exact confirmation"):
        execute_lifecycle(request("delete_business", "acme_business"), FakeSql())


@pytest.mark.parametrize(
    ("command", "entity_id", "confirmation"),
    [
        ("delete_agency", "n8iv_promotions", "DELETE AGENCY n8iv_promotions"),
        ("delete_business", "demo_client", "DELETE BUSINESS demo_client"),
    ],
)
def test_protected_entities_cannot_be_deleted(command, entity_id, confirmation):
    with pytest.raises(ValueError, match="Protected"):
        execute_lifecycle(
            request(command, entity_id, confirmation=confirmation),
            FakeSql(),
        )


def test_agency_delete_is_blocked_while_businesses_remain():
    sql = FakeSql(agency={"agency_id": "acme_agency"}, active_clients=2)
    with pytest.raises(ValueError, match="active businesses"):
        execute_lifecycle(
            request(
                "delete_agency",
                "acme_agency",
                confirmation="DELETE AGENCY acme_agency",
            ),
            sql,
        )
    assert not any(statement.startswith("DROP SCHEMA") for statement in sql.statements)


def test_business_delete_rejects_registry_schema_mismatch():
    sql = FakeSql(
        business={
            "client_id": "acme_business",
            "config_json": json.dumps(
                {"databricks_schema": "workspace.attribution_someone_else"}
            ),
        }
    )
    with pytest.raises(ValueError, match="does not match"):
        execute_lifecycle(
            request(
                "delete_business",
                "acme_business",
                confirmation="DELETE BUSINESS acme_business",
            ),
            sql,
        )
    assert not any(statement.startswith("DROP SCHEMA") for statement in sql.statements)


def test_valid_business_delete_emits_one_exact_drop():
    sql = FakeSql(
        business={
            "client_id": "acme_business",
            "config_json": json.dumps(
                {"databricks_schema": "workspace.attribution_acme_business"}
            ),
        }
    )
    result = execute_lifecycle(
        request(
            "delete_business",
            "acme_business",
            confirmation="DELETE BUSINESS acme_business",
        ),
        sql,
    )
    drops = [
        statement for statement in sql.statements if statement.startswith("DROP SCHEMA")
    ]
    assert drops == [
        "DROP SCHEMA IF EXISTS workspace.attribution_acme_business CASCADE"
    ]
    assert result["status"] == "succeeded"


def test_succeeded_request_is_idempotent_and_does_not_repeat_ddl():
    previous = {
        "request_id": "request-12345678",
        "status": "succeeded",
        "result_json": json.dumps({"status": "succeeded", "business_id": "acme"}),
    }
    sql = FakeSql(prior_operation=previous)
    result = execute_lifecycle(request("create_business", "acme"), sql)
    assert result["business_id"] == "acme"
    assert not any(
        statement.startswith(
            ("CREATE SCHEMA IF NOT EXISTS workspace.attribution_acme", "DROP SCHEMA")
        )
        for statement in sql.statements
    )
