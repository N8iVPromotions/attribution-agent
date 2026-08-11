"""Post-array agency benchmark finalizer for Cloud Run Jobs."""

from __future__ import annotations

import argparse
import logging
import os

from config.agency_config import get_agency
from flows.agency_flow import run_agency_benchmark_sql
from utils.work_manifest import load_work_manifest

logger = logging.getLogger(__name__)


def finalize_manifest(manifest_uri: str) -> dict:
    manifest = load_work_manifest(manifest_uri)
    agency_ids = sorted({item["agency_id"] for item in manifest.get("work_items", [])})
    if manifest.get("dry_run"):
        logger.info("[Finalizer] Dry run; benchmark SQL skipped")
        return {"status": "dry_run", "agencies": agency_ids}

    for agency_id in agency_ids:
        run_agency_benchmark_sql(get_agency(agency_id), fail_fast=True)
    return {"status": "complete", "agencies": agency_ids}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest-uri",
        default=os.environ.get("ARIE_WORK_MANIFEST_URI", ""),
    )
    args = parser.parse_args()
    if not args.manifest_uri:
        parser.error("--manifest-uri or ARIE_WORK_MANIFEST_URI is required")
    logger.info("[Finalizer] %s", finalize_manifest(args.manifest_uri))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
