from __future__ import annotations

import pytest

from flows import agency_flow, benchmark_finalizer, job_launcher
from utils import work_manifest


def test_manifest_rejects_duplicate_client_assignments():
    with pytest.raises(ValueError, match="duplicate client_id"):
        work_manifest._validate_items(
            [("agency-a", "client-a"), ("agency-b", "client-a")]
        )


def test_cloud_task_uses_immutable_manifest(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "1")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    monkeypatch.setenv("ARIE_WORK_MANIFEST_URI", "gs://bucket/manifest.json")
    manifest = {
        "run_id": "manifest-run",
        "dry_run": True,
        "attribution_model": "linear",
        "work_items": [
            {"task_index": 0, "agency_id": "a", "client_id": "c1"},
            {"task_index": 1, "agency_id": "a", "client_id": "c2"},
        ],
    }
    monkeypatch.setattr(work_manifest, "load_work_manifest", lambda _uri: manifest)
    calls = []
    monkeypatch.setattr(
        agency_flow,
        "run_agency_pipeline",
        lambda **kwargs: calls.append(kwargs) or {"status": "complete"},
    )

    agency_flow.run_cloud_task(None, False, None, "last_touch")

    assert calls[0]["client_filter"] == ["c2"]
    assert calls[0]["execution_run_id"] == "manifest-run"
    assert calls[0]["dry_run"] is True
    assert calls[0]["attribution_model"] == "linear"


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self):
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return _Response(
            {
                "name": "operations/op-1",
                "done": True,
                "response": {
                    "name": "projects/p/locations/r/jobs/j/executions/e",
                    "completionTime": "2026-08-09T00:00:00Z",
                    "taskCount": 2,
                    "succeededCount": 2,
                },
            }
        )


def test_job_launcher_applies_manifest_and_task_count_overrides():
    session = _Session()

    execution = job_launcher.run_job(
        session,
        project_id="p",
        region="r",
        job_name="j",
        environment={"ARIE_WORK_MANIFEST_URI": "gs://b/m.json"},
        task_count=2,
    )

    request_body = session.requests[0][2]["json"]
    assert request_body["overrides"]["taskCount"] == 2
    assert request_body["overrides"]["containerOverrides"][0]["env"] == [
        {"name": "ARIE_WORK_MANIFEST_URI", "value": "gs://b/m.json"}
    ]
    assert execution["succeededCount"] == 2


def test_finalizer_skips_benchmarks_for_dry_run(monkeypatch):
    monkeypatch.setattr(
        benchmark_finalizer,
        "load_work_manifest",
        lambda _uri: {
            "dry_run": True,
            "work_items": [{"agency_id": "agency-a", "client_id": "client-a"}],
        },
    )
    calls = []
    monkeypatch.setattr(
        benchmark_finalizer,
        "run_agency_benchmark_sql",
        lambda *_a, **_k: calls.append(1),
    )

    result = benchmark_finalizer.finalize_manifest("gs://bucket/manifest.json")

    assert result == {"status": "dry_run", "agencies": ["agency-a"]}
    assert calls == []
