"""Shared bounded-memory Arrow batch helpers for ingestion connectors."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator

import pandas as pd
import pyarrow as pa

MIN_BATCH_ROWS = 5_000
MAX_BATCH_ROWS = 10_000


def configured_batch_rows() -> int:
    raw = os.environ.get("ARIE_INGEST_BATCH_ROWS", str(MIN_BATCH_ROWS))
    try:
        value = int(raw)
    except ValueError:
        value = MIN_BATCH_ROWS
    return min(max(value, MIN_BATCH_ROWS), MAX_BATCH_ROWS)


def records_to_batches(
    records: Iterable[dict], batch_rows: int | None = None
) -> Iterator[pa.RecordBatch]:
    """Yield 5k-10k RecordBatches without retaining prior rows."""
    size = batch_rows or configured_batch_rows()
    if not MIN_BATCH_ROWS <= size <= MAX_BATCH_ROWS:
        raise ValueError(
            f"batch_rows must be between {MIN_BATCH_ROWS} and {MAX_BATCH_ROWS}"
        )

    buffer: list[dict] = []
    for record in records:
        buffer.append(record)
        if len(buffer) == size:
            yield pa.RecordBatch.from_pylist(buffer)
            buffer.clear()
    if buffer:
        yield pa.RecordBatch.from_pylist(buffer)


def batches_to_dataframe(batches: Iterable[pa.RecordBatch]) -> pd.DataFrame:
    """Compatibility adapter for local/demo callers that still expect pandas."""
    frames = [batch.to_pandas() for batch in batches if batch.num_rows]
    return (
        pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    )
