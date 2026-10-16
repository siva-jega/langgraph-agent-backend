"""Phase 5 tests: CLI argparse wiring + HTTP trigger endpoint.

CLI tests don't actually hit LangSmith — the runner / dataset helpers are
stubbed so we just verify the argparse plumbing routes to the right callable
with the right args.

HTTP tests use FastAPI's TestClient with a stubbed ``run_dataset`` so the
background task completes synchronously and we can assert against the
registry state.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.evals.cli import _build_parser, main as cli_main
from app.modules.evals.route import _reset_registry_for_tests, router as evals_router


# ---------------------------------------------------------------------------
# CLI — argparse routing
# ---------------------------------------------------------------------------


def test_cli_parser_requires_subcommand() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_cli_upload_dataset_parses_required_args() -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "upload-dataset",
        "--name", "web_agent_v1",
        "--path", "/tmp/cases.json",
    ])
    assert args.command == "upload-dataset"
    assert args.name == "web_agent_v1"
    assert args.path == Path("/tmp/cases.json")
    assert args.dry_run is False


def test_cli_run_parses_optional_metadata_flags() -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "run",
        "--dataset", "web_agent_v1",
        "--experiment", "my-tweak",
        "--git-sha", "abc1234",
        "--note", "trying out a new persona block",
        "--no-judges",
        "--preview",
        "--concurrency", "4",
    ])
    assert args.dataset == "web_agent_v1"
    assert args.experiment == "my-tweak"
    assert args.git_sha == "abc1234"
    assert args.note.startswith("trying out")
    assert args.no_judges is True
    assert args.preview is True
    assert args.concurrency == 4


def test_cli_upload_dry_run_does_not_call_uploader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """--dry-run parses + validates the JSON but skips the LangSmith upload."""
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps(
            {
                "version": "test-v0",
                "cases": [
                    {
                        "id": "c1",
                        "query": "hi",
                        "agent_config_template": "shopify_only",
                    }
                ],
            }
        )
    )

    upload_called = {"count": 0}

    def _spy(*args, **kwargs):
        upload_called["count"] += 1
        return "ds-id"

    import app.modules.evals.dataset as dataset_module

    monkeypatch.setattr(dataset_module, "upload_to_langsmith", _spy)

    exit_code = cli_main([
        "upload-dataset", "--name", "x", "--path", str(dataset), "--dry-run",
    ])
    assert exit_code == 0
    assert upload_called["count"] == 0
    out = capsys.readouterr().out
    assert "Loaded 1 cases" in out
    assert "not uploading" in out


def test_cli_run_invokes_runner_with_metadata(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The run subcommand collects --git-sha / --note into experiment metadata."""
    captured: dict = {}

    async def fake_run_dataset(dataset, **kwargs):
        captured["dataset"] = dataset
        captured["kwargs"] = kwargs

        class _R:
            experiment_name = kwargs.get("experiment_prefix")

        return _R()

    import app.modules.evals.runner as runner_module

    monkeypatch.setattr(runner_module, "run_dataset", fake_run_dataset)

    exit_code = cli_main([
        "run",
        "--dataset", "web_agent_v1",
        "--experiment", "expt-1",
        "--no-judges",
        "--git-sha", "abc1234",
        "--note", "hello",
    ])
    assert exit_code == 0
    assert captured["dataset"] == "web_agent_v1"
    kw = captured["kwargs"]
    assert kw["experiment_prefix"] == "expt-1"
    assert kw["evaluators"] == []  # --no-judges → empty list, not default bundle
    assert kw["metadata"] == {"git_sha": "abc1234", "note": "hello"}


def test_cli_open_emits_url(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """open subcommand prints a URL; --no-browser skips the actual launch."""
    from app.core.config import app_settings

    monkeypatch.setattr(app_settings, "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com", raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_PROJECT", "web-agent-evals", raising=False)

    exit_code = cli_main(["open", "my-expt", "--no-browser"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "smith.langchain.com" in out
    assert "my-expt" in out
    assert "web-agent-evals" in out


# ---------------------------------------------------------------------------
# HTTP trigger — POST /evals/runs + GET /evals/runs/{id}
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_evals_router() -> FastAPI:
    """Minimal FastAPI app mounting only the evals router so the test doesn't
    pull in the entire application surface (DB pools, OpenTelemetry, etc.)."""
    _reset_registry_for_tests()
    app = FastAPI()
    app.include_router(evals_router, prefix="/api")
    return app


@pytest.fixture
def configured_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set the internal secret resolver to a known value for the test."""
    import app.modules.evals.route as route_module

    monkeypatch.setattr(
        route_module,
        "resolved_nestjs_internal_secret",
        lambda: "test-secret-123",
    )
    return "test-secret-123"


@pytest.fixture
def stub_run_dataset(monkeypatch: pytest.MonkeyPatch):
    """Replace ``run_dataset`` so the background task completes instantly."""
    captured: dict = {}

    async def fake(dataset, **kwargs):
        captured["dataset"] = dataset
        captured["kwargs"] = kwargs

        class _R:
            experiment_name = kwargs.get("experiment_prefix")

        return _R()

    import app.modules.evals.runner as runner_module

    monkeypatch.setattr(runner_module, "run_dataset", fake)
    return captured


def _wait_for_status(client: TestClient, secret: str, run_id: str, target: str,
                     timeout: float = 2.0) -> dict:
    """Poll until status leaves 'pending' or timeout. BackgroundTasks runs after
    the response, so the registry transitions out-of-band."""
    import time

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/api/evals/runs/{run_id}", headers={"x-internal-secret": secret})
        last = resp.json()
        if last.get("status") == target:
            return last
        time.sleep(0.05)
    return last


def test_trigger_eval_returns_202_and_run_id(
    app_with_evals_router: FastAPI, configured_secret: str, stub_run_dataset
) -> None:
    client = TestClient(app_with_evals_router)
    resp = client.post(
        "/api/evals/runs",
        json={"dataset": "web_agent_v1", "experiment_prefix": "test"},
        headers={"x-internal-secret": configured_secret},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"]
    assert body["dataset"] == "web_agent_v1"
    assert body["status"] in {"pending", "running", "succeeded"}


def test_trigger_eval_rejects_missing_auth(
    app_with_evals_router: FastAPI, configured_secret: str, stub_run_dataset
) -> None:
    client = TestClient(app_with_evals_router)
    resp = client.post("/api/evals/runs", json={"dataset": "x"})
    assert resp.status_code == 401


def test_trigger_eval_rejects_wrong_secret(
    app_with_evals_router: FastAPI, configured_secret: str, stub_run_dataset
) -> None:
    client = TestClient(app_with_evals_router)
    resp = client.post(
        "/api/evals/runs",
        json={"dataset": "x"},
        headers={"x-internal-secret": "wrong"},
    )
    assert resp.status_code == 401


def test_trigger_eval_503_when_internal_secret_not_configured(
    app_with_evals_router: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the deployment hasn't set INTERNAL_SECRET we fail closed (503) instead
    of accepting every request."""
    import app.modules.evals.route as route_module

    monkeypatch.setattr(route_module, "resolved_nestjs_internal_secret", lambda: "")
    client = TestClient(app_with_evals_router)
    resp = client.post(
        "/api/evals/runs",
        json={"dataset": "x"},
        headers={"x-internal-secret": "anything"},
    )
    assert resp.status_code == 503


def test_trigger_then_status_round_trip_succeeds(
    app_with_evals_router: FastAPI, configured_secret: str, stub_run_dataset
) -> None:
    client = TestClient(app_with_evals_router)
    resp = client.post(
        "/api/evals/runs",
        json={
            "dataset": "web_agent_v1",
            "experiment_prefix": "round-trip",
            "no_judges": True,
            "metadata": {"git_sha": "abc"},
        },
        headers={"x-internal-secret": configured_secret},
    )
    run_id = resp.json()["run_id"]

    final = _wait_for_status(client, configured_secret, run_id, "succeeded")
    assert final["status"] == "succeeded", final
    assert final["dataset"] == "web_agent_v1"
    # finished_at is set after the background task completes.
    assert final["finished_at"]

    # Stub was invoked with our params.
    assert stub_run_dataset["dataset"] == "web_agent_v1"
    assert stub_run_dataset["kwargs"]["evaluators"] == []
    assert stub_run_dataset["kwargs"]["metadata"]["git_sha"] == "abc"


def test_status_endpoint_404_for_unknown_run(
    app_with_evals_router: FastAPI, configured_secret: str, stub_run_dataset
) -> None:
    client = TestClient(app_with_evals_router)
    resp = client.get(
        "/api/evals/runs/nonexistent-id",
        headers={"x-internal-secret": configured_secret},
    )
    assert resp.status_code == 404


def test_run_failure_returns_failed_status(
    app_with_evals_router: FastAPI, configured_secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed run_dataset call must show up as status=failed with the error,
    not as a silent 200 followed by mystery."""

    async def boom(dataset, **kwargs):
        raise RuntimeError("simulated explosion")

    import app.modules.evals.runner as runner_module

    monkeypatch.setattr(runner_module, "run_dataset", boom)

    client = TestClient(app_with_evals_router)
    resp = client.post(
        "/api/evals/runs",
        json={"dataset": "x"},
        headers={"x-internal-secret": configured_secret},
    )
    run_id = resp.json()["run_id"]

    final = _wait_for_status(client, configured_secret, run_id, "failed")
    assert final["status"] == "failed", final
    assert "simulated explosion" in (final.get("error") or "")
