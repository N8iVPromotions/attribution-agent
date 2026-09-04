from __future__ import annotations

import pytest
from google.cloud import storage

from flows import agency_flow, benchmark_finalizer, job_launcher
from utils import work_manifest


class _Blob:
    def __init__(self, downloaded=""):
        self.uploaded = None
        self.downloaded = downloaded

    def upload_from_string(self, payload, **kwargs):
        self.uploaded = (payload, kwargs)

    def download_as_text(self):
        return self.downloaded


class _Bucket:
    def __init__(self, blob):
        self._blob = blob

    def blob(self, _name):
        return self._blob


class _StorageClient:
    def __init__(self, blob):
        self._blob = blob

    def bucket(self, _name):
        return _Bucket(self._blob)


def test_manifest_rejects_duplicate_client_assignments():
    with pytest.raises(ValueError, match="duplicate client_id"):
        work_manifest._validate_items(
            [("agency-a", "client-a"), ("agency-b", "client-a")]
        )


def test_manifest_rejects_unobserved_attribution_model():
    with pytest.raises(ValueError, match="only last_touch"):
        work_manifest.create_work_manifest(
            [("agency-a", "client-a")],
            attribution_model="linear",
            bucket_name="manifest-bucket",
        )


def test_manifest_persists_explicit_report_month(monkeypatch):
    blob = _Blob()
    monkeypatch.setattr(storage, "Client", lambda: _StorageClient(blob))

    uri, payload = work_manifest.create_work_manifest(
        [("agency-a", "client-a")],
        run_id="stable-run",
        report_month="2026-08",
        bucket_name="manifest-bucket",
    )

    assert uri == "gs://manifest-bucket/work-manifests/run_id=stable-run/manifest.json"
    assert payload["version"] == 2
    assert payload["report_month"] == "2026-08"
    assert payload["report_timezone"] == "America/New_York"
    assert payload["period_start"] == "2026-08-01T04:00:00+00:00"
    assert payload["period_end"] == "2026-09-01T04:00:00+00:00"
    assert '"report_month":"2026-08"' in blob.uploaded[0]


def test_legacy_manifest_without_period_fails_closed(monkeypatch):
    blob = _Blob(
        '{"version":1,"work_items":['
        '{"task_index":0,"agency_id":"agency-a","client_id":"client-a"}]}'
    )
    monkeypatch.setattr(storage, "Client", lambda: _StorageClient(blob))

    with pytest.raises(ValueError, match="version 2"):
        work_manifest.load_work_manifest("gs://manifest-bucket/manifest.json")


def test_version_two_manifest_requires_complete_period(monkeypatch):
    blob = _Blob(
        '{"version":2,"attribution_model":"last_touch","work_items":['
        '{"task_index":0,"agency_id":"agency-a","client_id":"client-a"}]}'
    )
    monkeypatch.setattr(storage, "Client", lambda: _StorageClient(blob))

    with pytest.raises(ValueError, match="complete report period"):
        work_manifest.load_work_manifest("gs://manifest-bucket/manifest.json")


def test_loaded_manifest_rejects_unobserved_attribution_model(monkeypatch):
    blob = _Blob(
        '{"version":2,"attribution_model":"w_shape","report_month":"2026-08",'
        '"report_timezone":"America/New_York",'
        '"period_start":"2026-08-01T04:00:00+00:00",'
        '"period_end":"2026-09-01T04:00:00+00:00","work_items":['
        '{"task_index":0,"agency_id":"agency-a","client_id":"client-a"}]}'
    )
    monkeypatch.setattr(storage, "Client", lambda: _StorageClient(blob))

    with pytest.raises(ValueError, match="only last_touch"):
        work_manifest.load_work_manifest("gs://manifest-bucket/manifest.json")


def test_cloud_task_uses_immutable_manifest(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "1")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    monkeypatch.setenv("ARIE_WORK_MANIFEST_URI", "gs://bucket/manifest.json")
    manifest = {
        "run_id": "manifest-run",
        "dry_run": True,
        "attribution_model": "last_touch",
        "report_month": "2026-08",
        "report_timezone": "America/New_York",
        "period_start": "2026-08-01T04:00:00+00:00",
        "period_end": "2026-09-01T04:00:00+00:00",
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
    assert calls[0]["attribution_model"] == "last_touch"
    assert calls[0]["report_month"] == "2026-08"


def test_cloud_task_rejects_period_environment_that_conflicts_with_manifest(
    monkeypatch,
):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "1")
    monkeypatch.setenv("ARIE_WORK_MANIFEST_URI", "gs://bucket/manifest.json")
    monkeypatch.setenv("ARIE_REPORT_MONTH", "2026-07")
    manifest = {
        "run_id": "manifest-run",
        "dry_run": True,
        "attribution_model": "last_touch",
        "report_month": "2026-08",
        "report_timezone": "America/New_York",
        "period_start": "2026-08-01T04:00:00+00:00",
        "period_end": "2026-09-01T04:00:00+00:00",
        "work_items": [
            {"task_index": 0, "agency_id": "a", "client_id": "c1"},
        ],
    }
    monkeypatch.setattr(work_manifest, "load_work_manifest", lambda _uri: manifest)

    with pytest.raises(RuntimeError, match="immutable work manifest"):
        agency_flow.run_cloud_task(None, False, None, "last_touch")


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


def test_job_launcher_forwards_one_report_month_to_both_jobs(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")
    monkeypatch.setattr(
        job_launcher, "build_client_work_items", lambda *_args: [("agency", "client")]
    )
    captured_manifest = {}

    def create_manifest(_items, **kwargs):
        captured_manifest.update(kwargs)
        return "gs://bucket/manifest.json", {"run_id": "run"}

    monkeypatch.setattr(job_launcher, "create_work_manifest", create_manifest)
    monkeypatch.setattr(job_launcher, "_session", lambda: object())
    calls = []
    monkeypatch.setattr(
        job_launcher,
        "run_job",
        lambda _session, **kwargs: calls.append(kwargs) or {"name": kwargs["job_name"]},
    )

    result = job_launcher.launch(
        agency_id="agency",
        client_filter=None,
        dry_run=True,
        attribution_model="last_touch",
        report_month="2026-08",
    )

    assert captured_manifest["report_month"] == "2026-08"
    assert calls[0]["environment"]["ARIE_REPORT_MONTH"] == "2026-08"
    assert calls[0]["environment"]["ARIE_REPORT_TIMEZONE"] == "America/New_York"
    assert calls[0]["environment"]["ARIE_PERIOD_START"] == ("2026-08-01T04:00:00+00:00")
    assert calls[1]["environment"]["ARIE_REPORT_MONTH"] == "2026-08"
    assert result["report_month"] == "2026-08"


def test_job_launcher_rejects_unobserved_model_before_dispatch():
    with pytest.raises(ValueError, match="only last_touch"):
        job_launcher.launch(
            agency_id="agency",
            client_filter=None,
            dry_run=True,
            attribution_model="w_shape",
        )


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
