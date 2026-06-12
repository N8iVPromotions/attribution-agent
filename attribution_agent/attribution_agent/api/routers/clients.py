from __future__ import annotations
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException
from api.auth import require_auth, require_admin
from api.models import ClientConfigRequest, ClientConfigResponse
from config.rbac_config import Role, Permission, require_permission

router = APIRouter(prefix="/clients", tags=["clients"])


def _to_response(cfg) -> ClientConfigResponse:
    return ClientConfigResponse(
        client_id=cfg.client_id,
        client_name=cfg.client_name,
        attribution_model=cfg.attribution_model,
        meta_enabled=cfg.meta_enabled,
        google_ads_enabled=cfg.google_ads_enabled,
        linkedin_ads_enabled=cfg.linkedin_ads_enabled,
        hubspot_enabled=cfg.hubspot_enabled,
        stripe_enabled=cfg.stripe_enabled,
        lookback_days=cfg.lookback_days,
        client_report_email=cfg.client_report_email,
        agency_id=cfg.agency_id,
        databricks_schema=cfg.databricks_schema,
    )


@router.get("", response_model=list[ClientConfigResponse])
async def list_clients(role: Role = Depends(require_auth)) -> list[ClientConfigResponse]:
    require_permission(role, Permission.VIEW_REPORTS)
    from config.client_config import CLIENT_REGISTRY, reload_client_registry
    reload_client_registry()
    return [_to_response(c) for c in CLIENT_REGISTRY.values()]


@router.get("/{client_id}", response_model=ClientConfigResponse)
async def get_client(client_id: str, role: Role = Depends(require_auth)) -> ClientConfigResponse:
    require_permission(role, Permission.VIEW_REPORTS)
    from config.client_config import CLIENT_REGISTRY, reload_client_registry
    reload_client_registry()
    cfg = CLIENT_REGISTRY.get(client_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    return _to_response(cfg)


@router.post("", response_model=ClientConfigResponse, status_code=201)
async def create_client(
    req: ClientConfigRequest,
    role: Role = Depends(require_admin),
) -> ClientConfigResponse:
    require_permission(role, Permission.MANAGE_CLIENTS)
    from config.client_config import (
        ClientConfig, save_client_config, slugify_client_id, default_client_schema,
    )
    client_id = slugify_client_id(req.client_name)
    cfg = ClientConfig(
        client_id=client_id,
        client_name=req.client_name,
        attribution_model=req.attribution_model,
        meta_enabled=req.meta_enabled,
        meta_ad_account_id=req.meta_ad_account_id,
        google_ads_enabled=req.google_ads_enabled,
        google_ads_customer_id=req.google_ads_customer_id,
        linkedin_ads_enabled=req.linkedin_ads_enabled,
        linkedin_ads_account_id=req.linkedin_ads_account_id,
        hubspot_enabled=req.hubspot_enabled,
        hubspot_pipeline_id=req.hubspot_pipeline_id,
        stripe_enabled=req.stripe_enabled,
        stripe_account_id=req.stripe_account_id,
        lookback_days=req.lookback_days,
        client_report_email=req.client_report_email,
        client_display_name=req.client_display_name,
        agency_id=req.agency_id,
        databricks_schema=default_client_schema(client_id),
    )
    save_client_config(cfg)
    return _to_response(cfg)


@router.put("/{client_id}", response_model=ClientConfigResponse)
async def update_client(
    client_id: str,
    req: ClientConfigRequest,
    role: Role = Depends(require_admin),
) -> ClientConfigResponse:
    require_permission(role, Permission.MANAGE_CLIENTS)
    from config.client_config import CLIENT_REGISTRY, reload_client_registry, save_client_config
    reload_client_registry()
    existing = CLIENT_REGISTRY.get(client_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    from dataclasses import replace
    updated = replace(
        existing,
        client_name=req.client_name,
        attribution_model=req.attribution_model,
        meta_enabled=req.meta_enabled,
        meta_ad_account_id=req.meta_ad_account_id,
        google_ads_enabled=req.google_ads_enabled,
        google_ads_customer_id=req.google_ads_customer_id,
        linkedin_ads_enabled=req.linkedin_ads_enabled,
        linkedin_ads_account_id=req.linkedin_ads_account_id,
        hubspot_enabled=req.hubspot_enabled,
        hubspot_pipeline_id=req.hubspot_pipeline_id,
        stripe_enabled=req.stripe_enabled,
        stripe_account_id=req.stripe_account_id,
        lookback_days=req.lookback_days,
        client_report_email=req.client_report_email,
        client_display_name=req.client_display_name,
        agency_id=req.agency_id,
    )
    save_client_config(updated)
    return _to_response(updated)


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: str,
    role: Role = Depends(require_admin),
) -> None:
    require_permission(role, Permission.MANAGE_CLIENTS)
    from config.client_config import CLIENT_REGISTRY, reload_client_registry, delete_client_config, is_custom_client
    reload_client_registry()
    if client_id not in CLIENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    if not is_custom_client(client_id):
        raise HTTPException(status_code=403, detail="Cannot delete built-in clients")
    delete_client_config(client_id)
