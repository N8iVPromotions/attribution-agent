"""Distributed per-client execution leases backed by GCS generations."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Self

logger = logging.getLogger(__name__)


class ClientLeaseHeld(RuntimeError):
    """Raised when another execution owns a live client lease."""


class ClientLeaseLost(RuntimeError):
    """Raised when a running execution can no longer renew its lease."""


def _is_enabled() -> bool:
    configured = os.environ.get("ARIE_CLIENT_LOCKS_ENABLED")
    if configured is not None:
        return configured.lower() == "true"
    return bool(os.environ.get("CLOUD_RUN_JOB"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ClientLease:
    def __init__(self, client_id: str, run_id: str, ttl_seconds: int = 300) -> None:
        self.client_id = client_id
        self.run_id = run_id
        self.ttl_seconds = max(ttl_seconds, 60)
        self.enabled = _is_enabled()
        self.owner_id = os.environ.get("CLOUD_RUN_TASK_INDEX") or uuid.uuid4().hex
        self.generation: int | None = None
        self._blob = None
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._heartbeat: threading.Thread | None = None
        self._mutex = threading.Lock()

    def acquire(self) -> Self:
        if not self.enabled:
            return self
        if self.generation is not None:
            return self
        self._acquire()
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name=f"lease-{self.client_id}",
            daemon=True,
        )
        self._heartbeat.start()
        return self

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if not self.enabled:
            return False
        self._stop.set()
        if self._heartbeat:
            self._heartbeat.join(timeout=5)
        self._release()
        if exc_type is None and self._lost.is_set():
            raise ClientLeaseLost(f"Lost execution lease for client '{self.client_id}'")
        return False

    def _acquire(self) -> None:
        from google.api_core.exceptions import PreconditionFailed
        from google.cloud import storage

        bucket_name = os.environ.get("ARIE_CLIENT_LOCK_BUCKET", "").strip()
        if not bucket_name:
            raise RuntimeError(
                "ARIE_CLIENT_LOCK_BUCKET is required when client locks are enabled"
            )

        safe_client_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in self.client_id
        )
        self._blob = (
            storage.Client()
            .bucket(bucket_name)
            .blob(f"locks/client-{safe_client_id}.json")
        )

        for attempt in range(2):
            try:
                self._write(if_generation_match=0)
                logger.info(
                    "[Lease] Acquired client=%s run=%s", self.client_id, self.run_id
                )
                return
            except PreconditionFailed:
                if attempt or not self._remove_expired_lock():
                    raise ClientLeaseHeld(
                        f"Client '{self.client_id}' is already being processed"
                    ) from None

    def _payload(self) -> str:
        now = _utcnow()
        return json.dumps(
            {
                "client_id": self.client_id,
                "run_id": self.run_id,
                "owner_id": self.owner_id,
                "heartbeat_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
            }
        )

    def _write(self, if_generation_match: int) -> None:
        assert self._blob is not None
        self._blob.upload_from_string(
            self._payload(),
            content_type="application/json",
            if_generation_match=if_generation_match,
        )
        self.generation = int(self._blob.generation)

    def _remove_expired_lock(self) -> bool:
        from google.api_core.exceptions import NotFound, PreconditionFailed

        assert self._blob is not None
        try:
            self._blob.reload()
            generation = int(self._blob.generation)
            payload = json.loads(self._blob.download_as_text())
            expires_at = datetime.fromisoformat(payload["expires_at"])
            if expires_at > _utcnow():
                return False
            self._blob.delete(if_generation_match=generation)
            return True
        except (KeyError, ValueError, TypeError, NotFound, PreconditionFailed):
            return False

    def _heartbeat_loop(self) -> None:
        interval = max(self.ttl_seconds // 3, 20)
        while not self._stop.wait(interval):
            try:
                with self._mutex:
                    if self.generation is None:
                        return
                    self._write(if_generation_match=self.generation)
            except Exception as exc:
                logger.error("[Lease] Heartbeat failed for %s: %s", self.client_id, exc)
                self._lost.set()
                return

    def _release(self) -> None:
        from google.api_core.exceptions import NotFound, PreconditionFailed

        if self._blob is None or self.generation is None:
            return
        try:
            with self._mutex:
                self._blob.delete(if_generation_match=self.generation)
            logger.info(
                "[Lease] Released client=%s run=%s", self.client_id, self.run_id
            )
        except (NotFound, PreconditionFailed):
            self._lost.set()


def client_lease(client_id: str, run_id: str) -> ClientLease:
    ttl = int(os.environ.get("ARIE_CLIENT_LOCK_TTL_SECONDS", "300") or "300")
    return ClientLease(client_id=client_id, run_id=run_id, ttl_seconds=ttl)
