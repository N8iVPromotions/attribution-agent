"""
config/rbac_config.py
----------------------
Role-Based Access Control definitions.

This module is the authority on roles and permissions.
It is pure Python with no external dependencies — it defines the rules
but does not enforce them at the transport layer (enforcement lives in
api/auth.py for the REST API and is checked inline in the Streamlit app).
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN         = "admin"
    AGENCY_OWNER  = "agency_owner"
    ANALYST       = "analyst"
    VIEWER        = "viewer"


class Permission(str, Enum):
    RUN_PIPELINE_LIVE  = "run_pipeline_live"
    RUN_PIPELINE_DRY   = "run_pipeline_dry"
    VIEW_REPORTS       = "view_reports"
    MANAGE_CLIENTS     = "manage_clients"
    MANAGE_AGENCIES    = "manage_agencies"
    VIEW_AUDIT_LOG     = "view_audit_log"
    MANAGE_PROMPTS     = "manage_prompts"
    APPROVE_ACTIONS    = "approve_actions"
    MANAGE_EXPERIMENTS = "manage_experiments"
    VIEW_COST_DATA     = "view_cost_data"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),

    Role.AGENCY_OWNER: frozenset({
        Permission.RUN_PIPELINE_LIVE,
        Permission.RUN_PIPELINE_DRY,
        Permission.VIEW_REPORTS,
        Permission.MANAGE_CLIENTS,
        Permission.APPROVE_ACTIONS,
        Permission.VIEW_COST_DATA,
        Permission.MANAGE_EXPERIMENTS,
    }),

    Role.ANALYST: frozenset({
        Permission.RUN_PIPELINE_DRY,
        Permission.VIEW_REPORTS,
        Permission.VIEW_COST_DATA,
    }),

    Role.VIEWER: frozenset({
        Permission.VIEW_REPORTS,
    }),
}


def check_permission(role: Role, permission: Permission) -> bool:
    """Return True if the role has the given permission."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require_permission(role: Role, permission: Permission) -> None:
    """Raise PermissionError if the role does not have the given permission."""
    if not check_permission(role, permission):
        raise PermissionError(
            f"Role '{role.value}' does not have permission '{permission.value}'"
        )
