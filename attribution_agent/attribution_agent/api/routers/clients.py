from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from api.auth import require_auth, require_admin
from api.models import ClientConfigRequest, ClientConfigResponse
from config.rbac_config import Role, Permission, require_permission
from utils.secrets import redact_secrets

router = APIRouter(prefix="/clients", tags=["clients"])


def _to_response(cfg) -> ClientConfigResponse:
    return ClientConfigResponse(
        client_id=cfg.client_id,
        client_name=cfg.client_name,
        attribution_model=cfg.attribution_model,
        meta_enabled=cfg.meta_enabled,
        google_ads_enabled=cfg.google_ads_enabled,
        linkedin_ads_enabled=cfg.linkedin_ads_enabled,
        tiktok_ads_enabled=cfg.tiktok_ads_enabled,
        hubspot_enabled=cfg.hubspot_enabled,
        stripe_enabled=cfg.stripe_enabled,
        meta_ad_account_id=cfg.meta_ad_account_id,
        google_ads_customer_id=cfg.google_ads_customer_id,
        linkedin_ads_account_id=cfg.linkedin_ads_account_id,
        tiktok_ads_advertiser_id=cfg.tiktok_ads_advertiser_id,
        hubspot_pipeline_id=cfg.hubspot_pipeline_id,
        stripe_account_id=cfg.stripe_account_id,
        meta_secret_configured=bool(cfg.meta_access_token_secret_name),
        google_ads_secret_configured=bool(cfg.google_ads_refresh_token_secret_name),
        linkedin_ads_secret_configured=bool(cfg.linkedin_access_token_secret_name),
        tiktok_secret_configured=bool(cfg.tiktok_access_token_secret_name),
        hubspot_secret_configured=bool(cfg.hubspot_access_token_secret_name),
        stripe_secret_configured=bool(cfg.stripe_secret_key_secret_name),
        lookback_days=cfg.lookback_days,
        client_report_email=cfg.client_report_email,
        client_display_name=cfg.client_display_name,
        agency_id=cfg.agency_id,
        databricks_schema=cfg.databricks_schema,
    )


def _secret_values_from_request(req: ClientConfigRequest) -> dict[str, str]:
    return {
        "meta_access_token": req.meta_access_token,
        "google_ads_refresh_token": req.google_ads_refresh_token,
        "linkedin_access_token": req.linkedin_access_token,
        "tiktok_access_token": req.tiktok_access_token,
        "hubspot_access_token": req.hubspot_access_token,
        "stripe_secret_key": req.stripe_secret_key,
    }


@router.get("", response_model=list[ClientConfigResponse])
async def list_clients(
    role: Role = Depends(require_auth),
) -> list[ClientConfigResponse]:
    require_permission(role, Permission.VIEW_REPORTS)
    from config.client_config import CLIENT_REGISTRY, reload_client_registry

    reload_client_registry()
    return [_to_response(c) for c in CLIENT_REGISTRY.values()]


@router.get("/{client_id}", response_model=ClientConfigResponse)
async def get_client(
    client_id: str, role: Role = Depends(require_auth)
) -> ClientConfigResponse:
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
        ClientConfig,
        attach_client_secret_values,
        save_client_config,
        slugify_client_id,
        default_client_schema,
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
        tiktok_ads_enabled=req.tiktok_ads_enabled,
        tiktok_ads_advertiser_id=req.tiktok_ads_advertiser_id,
        hubspot_enabled=req.hubspot_enabled,
        hubspot_pipeline_id=req.hubspot_pipeline_id,
        stripe_enabled=req.stripe_enabled,
        stripe_account_id=req.stripe_account_id,
        lookback_days=req.lookback_days,
        client_report_email=req.client_report_email,
        client_display_name=req.client_display_name,
        agency_id=req.agency_id,
        databricks_schema=(req.databricks_schema or default_client_schema(client_id)),
    )
    try:
        cfg = attach_client_secret_values(cfg, _secret_values_from_request(req))
        save_client_config(cfg)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Client save failed: {redact_secrets(str(exc))}",
        ) from exc
    return _to_response(cfg)


@router.put("/{client_id}", response_model=ClientConfigResponse)
async def update_client(
    client_id: str,
    req: ClientConfigRequest,
    role: Role = Depends(require_admin),
) -> ClientConfigResponse:
    require_permission(role, Permission.MANAGE_CLIENTS)
    from config.client_config import (
        CLIENT_REGISTRY,
        attach_client_secret_values,
        reload_client_registry,
        save_client_config,
    )

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
        tiktok_ads_enabled=req.tiktok_ads_enabled,
        tiktok_ads_advertiser_id=req.tiktok_ads_advertiser_id,
        hubspot_enabled=req.hubspot_enabled,
        hubspot_pipeline_id=req.hubspot_pipeline_id,
        stripe_enabled=req.stripe_enabled,
        stripe_account_id=req.stripe_account_id,
        lookback_days=req.lookback_days,
        client_report_email=req.client_report_email,
        client_display_name=req.client_display_name,
        agency_id=req.agency_id,
        databricks_schema=req.databricks_schema or existing.databricks_schema,
    )
    try:
        updated = attach_client_secret_values(updated, _secret_values_from_request(req))
        save_client_config(updated)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Client save failed: {redact_secrets(str(exc))}",
        ) from exc
    return _to_response(updated)


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: str,
    role: Role = Depends(require_admin),
) -> None:
    require_permission(role, Permission.MANAGE_CLIENTS)
    from config.client_config import (
        CLIENT_REGISTRY,
        reload_client_registry,
        delete_client_config,
        is_custom_client,
    )

    reload_client_registry()
    if client_id not in CLIENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    if not is_custom_client(client_id):
        raise HTTPException(status_code=403, detail="Cannot delete built-in clients")
    delete_client_config(client_id)
