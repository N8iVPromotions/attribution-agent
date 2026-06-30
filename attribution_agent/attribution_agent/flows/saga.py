"""
flows/saga.py
-------------
Saga-style rollback for the attribution pipeline.

Usage:
    with PipelineSaga(run_id, client_id) as saga:
        saga.register_compensation("delete_delta_rows", lambda: _delete(...))
        step_result = do_step()

On any unhandled exception, registered compensations execute in LIFO
order. Failures are logged to audit_log as SAGA_COMPENSATION_FAILED.
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class PipelineSaga:
    def __init__(self, run_id: str, client_id: str, agency_id: str = "") -> None:
        self.run_id = run_id
        self.client_id = client_id
        self.agency_id = agency_id
        self._compensations: list[tuple[str, callable]] = []

    def register_compensation(self, name: str, fn: callable) -> None:
        self._compensations.append((name, fn))

    def __enter__(self) -> "PipelineSaga":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            return False

        logger.warning(
            f"[Saga] Rolling back {len(self._compensations)} step(s) "
            f"for run={self.run_id} client={self.client_id}: {exc_val}"
        )

        for name, fn in reversed(self._compensations):
            try:
                fn()
                logger.info(f"[Saga] Compensation '{name}' succeeded")
            except Exception as comp_exc:
                logger.error(f"[Saga] Compensation '{name}' FAILED: {comp_exc}")
                try:
                    from utils.audit_logger import log_event

                    log_event(
                        "SAGA_COMPENSATION_FAILED",
                        actor="system",
                        client_id=self.client_id,
                        agency_id=self.agency_id,
                        resource=name,
                        action="compensate",
                        outcome="failure",
                        detail={"error": str(comp_exc), "original_error": str(exc_val)},
                        run_id=self.run_id,
                    )
                except Exception:
                    pass

        return False  # re-raise the original exception
