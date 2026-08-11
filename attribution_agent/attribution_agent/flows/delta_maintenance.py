"""Weekly Delta compaction for active tenant schemas."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from config.client_config import get_client, list_clients  # noqa: E402
from utils.databricks_writer import (  # noqa: E402
    apply_liquid_clustering_v2,
    run_delta_maintenance,
)

logger = logging.getLogger(__name__)


def active_client_schemas() -> list[str]:
    return sorted(
        {get_client(client_id).databricks_schema for client_id in list_clients()}
    )


def maintain_active_schemas(*, migrate_v2: bool = False) -> dict:
    schemas = active_client_schemas()
    for schema in schemas:
        if migrate_v2:
            apply_liquid_clustering_v2(schema)
        run_delta_maintenance(schema)
        logger.info("[DeltaMaintenance] completed schema=%s", schema)
    return {"status": "complete", "schemas": schemas, "schema_count": len(schemas)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--migrate-v2",
        action="store_true",
        help="One-time DBR 18.1+ conversion before maintenance",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    maintain_active_schemas(migrate_v2=args.migrate_v2)


if __name__ == "__main__":
    main()
