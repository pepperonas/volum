"""The HTTP engine: a thin, authenticated shell over the service.

Everything here runs against the ASGI app in-process. The provider is the same
test double the service tests use — the engine's job is routing, auth, error
shape and streaming, not inference.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from conftest import make_png
from test_service import SlowFakeProvider, mark_installed
from volum_core.jobs import JobStatus
from volum_core.models.manager import InstallManifest
from volum_core.service import VolumService

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402 - after the skip guard

from volum_engine import create_app  # noqa: E402
from volum_engine.sse import EventSource  # noqa: E402

TOKEN = "t" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def provider() -> SlowFakeProvider:
    return SlowFakeProvider()


@pytest.fixture
def service(tmp_path: Path, provider: SlowFakeProvider) -> Iterator[VolumService]:
    svc = VolumService(
        data_dir=tmp_path / "data",
        settings_path=tmp_path / "settings.json",
        provider_factory=lambda model_id, manager, *, device: provider,
    )
    mark_installed(svc.models)
    yield svc
    provider.release.set()
    svc.shutdown()


@pytest.fixture
def client(service: VolumService) -> Iterator[TestClient]:
    with TestClient(create_app(service, token=TOKEN)) as test_client:
        yield test_client


@pytest.fixture
def image(tmp_path: Path) -> Path:
    return make_png(tmp_path / "photo.png")


def _events(client: TestClient, url: str, *, until_terminal: bool = True) -> list[dict]:  # type: ignore[type-arg]
    """Read an SSE stream into its parsed ``data:`` payloads."""
    payloads: list[dict] = []  # type: ignore[type-arg]
    with client.stream("GET", url, headers=AUTH) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                payloads.append(json.loads(line[5:]))
                if until_terminal and payloads[-1].get("status") in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    break
    return payloads


# --- authentication ----------------------------------------------------------------


def test_every_endpoint_requires_the_session_token(client: TestClient) -> None:
    """No unauthenticated local endpoint, /health included (docs/architecture.md)."""
    for path in ("/health", "/api/system/doctor", "/api/models", "/api/jobs"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["message"]


def test_a_wrong_token_is_rejected(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": "Bearer " + "x" * 48})
    assert response.status_code == 401


def test_the_right_token_is_accepted(client: TestClient) -> None:
    response = client.get("/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"]


def test_the_app_refuses_to_exist_without_a_token(service: VolumService) -> None:
    with pytest.raises(ValueError, match="token"):
        create_app(service, token="")
    with pytest.raises(ValueError, match="token"):
        create_app(service, token="short")  # noqa: S106 - the point of the test


# --- system ---------------------------------------------------------------------


def test_doctor_reports_hardware_and_every_model(client: TestClient) -> None:
    response = client.get("/api/system/doctor", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["hardware"]["os"]
    assert {a["provider_id"] for a in body["assessments"]} >= {"triposr", "trellis2"}


def test_settings_round_trip_without_leaking_the_token(client: TestClient) -> None:
    response = client.patch(
        "/api/settings",
        headers=AUTH,
        json={"hugging_face_token": "hf_secret", "allow_marginal_models": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hugging_face_token_set"] is True
    assert body["allow_marginal_models"] is True
    assert "hf_secret" not in response.text

    assert client.get("/api/settings", headers=AUTH).json()["hugging_face_token_set"] is True


# --- models -----------------------------------------------------------------------


def test_models_list_combines_catalogue_state_and_verdict(client: TestClient) -> None:
    response = client.get("/api/models", headers=AUTH)
    assert response.status_code == 200
    by_id = {entry["metadata"]["id"]: entry for entry in response.json()}
    assert by_id["triposr"]["install_state"] == "installed"
    assert by_id["trellis2"]["install_state"] == "not_installed"
    assert by_id["triposr"]["assessment"]["verdict"]


def test_an_unknown_model_is_404_in_the_error_shape(client: TestClient) -> None:
    response = client.get("/api/models/nope", headers=AUTH)
    assert response.status_code == 404
    error = response.json()["error"]
    assert "nope" in error["message"]
    assert "technical" in error and "suggestions" in error


def test_installing_an_installed_model_is_a_conflict(client: TestClient) -> None:
    response = client.post("/api/models/triposr/install", headers=AUTH, json={})
    assert response.status_code == 409


def test_install_starts_in_the_background_and_streams(
    client: TestClient, service: VolumService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_install(model_id: str, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["on_progress"]("weights", "Downloading model weights...")
        mark_installed(service.models, model_id)
        return InstallManifest(model_id=model_id, volum_version="0.1.0", python_version="3.11")

    monkeypatch.setattr(service.models, "install", fake_install)

    started = client.post("/api/models/trellis2/install", headers=AUTH, json={})
    assert started.status_code == 202
    assert started.json()["state"] in {"running", "done"}

    states = []
    with client.stream("GET", "/api/models/trellis2/install/events", headers=AUTH) as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                task = json.loads(line[5:])
                states.append(task["state"])
                if task["state"] != "running":
                    break
    assert states[-1] == "done"
    assert client.get("/api/models/trellis2", headers=AUTH).json()["install_state"] == "installed"


def test_removing_a_model(client: TestClient) -> None:
    response = client.delete("/api/models/triposr", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["removed"] is True
    entry = client.get("/api/models/triposr", headers=AUTH).json()
    assert entry["install_state"] == "not_installed"


# --- jobs -------------------------------------------------------------------------


def test_creating_a_job_returns_202_with_the_record_and_warnings(
    client: TestClient, image: Path, provider: SlowFakeProvider
) -> None:
    response = client.post(
        "/api/jobs",
        headers=AUTH,
        json={"model_id": "triposr", "images": [str(image)], "formats": ["stl"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["job"]["status"] in {"queued", "preprocessing", "reconstructing"}
    assert body["job"]["export_formats"] == ["stl"]
    assert any("size" in w.lower() for w in body["warnings"])
    provider.release.set()


def test_a_bad_image_is_400_with_the_input_message(client: TestClient, tmp_path: Path) -> None:
    fake = tmp_path / "x.png"
    fake.write_text("nope", encoding="utf-8")
    response = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(fake)]}
    )
    assert response.status_code == 400
    assert "PNG, JPEG or WebP" in response.json()["error"]["message"]


def test_an_uninstalled_model_is_409(client: TestClient, image: Path) -> None:
    response = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "trellis2", "images": [str(image)]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["suggestions"]


def test_a_malformed_body_is_422_in_the_same_error_shape(client: TestClient) -> None:
    """Validation failures are still errors a person can read (spec section 55)."""
    response = client.post("/api/jobs", headers=AUTH, json={"images": []})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["message"]
    assert "model_id" in error["technical"]


def test_the_job_stream_carries_every_stage_and_ends_at_completion(
    client: TestClient, image: Path, provider: SlowFakeProvider
) -> None:
    created = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(image)]}
    ).json()["job"]
    assert provider.started.wait(timeout=10)

    # Release once the stream is open, so the stream sees the transition live.
    threading.Timer(0.3, provider.release.set).start()
    events = _events(client, f"/api/jobs/{created['id']}/events")

    statuses = [event["status"] for event in events]
    assert statuses[0] == "reconstructing"
    assert statuses[-1] == "completed"
    assert "validating" in statuses
    assert events[-1]["artifacts"]["mesh"]


def test_the_stream_of_a_finished_job_is_its_snapshot_then_closes(
    client: TestClient, image: Path, provider: SlowFakeProvider
) -> None:
    provider.release.set()
    created = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(image)]}
    ).json()["job"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.get(f"/api/jobs/{created['id']}", headers=AUTH).json()["status"] == "completed":
            break
        time.sleep(0.02)

    events = _events(client, f"/api/jobs/{created['id']}/events", until_terminal=False)
    assert len(events) == 1
    assert events[0]["status"] == "completed"


def test_cancel_stops_a_running_job(
    client: TestClient, image: Path, provider: SlowFakeProvider
) -> None:
    created = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(image)]}
    ).json()["job"]
    assert provider.started.wait(timeout=10)

    response = client.post(f"/api/jobs/{created['id']}/cancel", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert provider.cancelled


def test_cancelling_an_unknown_job_is_404(client: TestClient) -> None:
    assert client.post("/api/jobs/deadbeef/cancel", headers=AUTH).status_code == 404


def test_artifacts_are_served_with_the_right_media_type(
    client: TestClient, image: Path, provider: SlowFakeProvider
) -> None:
    provider.release.set()
    created = client.post(
        "/api/jobs",
        headers=AUTH,
        json={"model_id": "triposr", "images": [str(image)], "formats": ["glb", "stl", "3mf"]},
    ).json()["job"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = client.get(f"/api/jobs/{created['id']}", headers=AUTH).json()
        if record["status"] == "completed":
            break
        time.sleep(0.02)
    assert record["status"] == "completed"

    mesh = client.get(f"/api/jobs/{created['id']}/artifacts/mesh", headers=AUTH)
    assert mesh.status_code == 200
    assert mesh.headers["content-type"] == "model/gltf-binary"
    assert mesh.content[:4] == b"glTF"

    stl = client.get(f"/api/jobs/{created['id']}/artifacts/export_stl", headers=AUTH)
    assert stl.headers["content-type"] == "model/stl"
    threemf = client.get(f"/api/jobs/{created['id']}/artifacts/export_3mf", headers=AUTH)
    assert threemf.headers["content-type"] == "model/3mf"
    report = client.get(f"/api/jobs/{created['id']}/artifacts/quality_report", headers=AUTH)
    assert report.headers["content-type"].startswith("application/json")
    assert report.json()["valid"] is True

    assert client.get(f"/api/jobs/{created['id']}/artifacts/nope", headers=AUTH).status_code == 404


def test_job_ids_are_validated_before_touching_the_filesystem(client: TestClient) -> None:
    assert client.get("/api/jobs/..%2F..%2Fetc", headers=AUTH).status_code == 404
    assert client.get("/api/jobs/not-hex-at-all", headers=AUTH).status_code == 404


def test_jobs_are_listed_newest_first(
    client: TestClient, image: Path, provider: SlowFakeProvider
) -> None:
    first = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(image)]}
    ).json()["job"]
    second = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(image)]}
    ).json()["job"]
    listed = client.get("/api/jobs?limit=10", headers=AUTH).json()
    assert [job["id"] for job in listed][:2] == [second["id"], first["id"]]
    provider.release.set()


def test_deleting_a_job_removes_its_directory_but_never_a_running_one(
    client: TestClient, image: Path, provider: SlowFakeProvider, service: VolumService
) -> None:
    created = client.post(
        "/api/jobs", headers=AUTH, json={"model_id": "triposr", "images": [str(image)]}
    ).json()["job"]
    assert provider.started.wait(timeout=10)

    assert client.delete(f"/api/jobs/{created['id']}", headers=AUTH).status_code == 409

    provider.release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = service.get_job(created["id"])
        if record is not None and record.status is JobStatus.COMPLETED:
            break
        time.sleep(0.02)

    assert client.delete(f"/api/jobs/{created['id']}", headers=AUTH).status_code == 200
    assert not (service.jobs_dir / created["id"]).exists()
    assert client.get(f"/api/jobs/{created['id']}", headers=AUTH).status_code == 404


# --- error handling -----------------------------------------------------------------


def test_an_unexpected_exception_is_a_generic_500_not_a_traceback(
    service: VolumService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(service, "list_models", boom)
    # The test client re-raises server errors unless told not to.
    with TestClient(create_app(service, token=TOKEN), raise_server_exceptions=False) as client:
        response = client.get("/api/models", headers=AUTH)
    assert response.status_code == 500
    error = response.json()["error"]
    assert "secret internal detail" not in error["message"]
    assert "RuntimeError" in error["technical"]


# --- streaming across the thread boundary -----------------------------------------


def test_each_update_is_snapshotted_when_it_happens_not_when_it_is_sent() -> None:
    """Seen live: seq 7 → 9 in a real run. The listener queued the *live* record
    and the pipeline thread appended the next entry before the event loop
    serialised it, so two frames carried the same state and one message was
    lost. Every frame must reflect the record as it was at notification time."""

    class Item(BaseModel):
        seq: int
        message: str
        done: bool = False

    listeners: list = []  # type: ignore[type-arg]

    def subscribe(listener):  # type: ignore[no-untyped-def]
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    class StubRequest:
        async def is_disconnected(self) -> bool:
            return False

    source: EventSource[Item] = EventSource(
        subscribe,
        accept=lambda _: True,
        sequence=lambda item: item.seq,
        is_final=lambda item: item.done,
    )

    async def run() -> list[str]:
        frames: list[str] = []
        gen = source.stream(StubRequest(), None)  # type: ignore[arg-type]
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)  # let the generator subscribe
        live = Item(seq=1, message="repairing")
        listeners[0](live)
        # The producer moves on before the consumer has serialised anything.
        live.seq = 2
        live.message = "validating"
        listeners[0](live)
        live.seq = 3
        live.message = "done"
        live.done = True
        listeners[0](live)
        frames.append((await first).decode())
        async for frame in gen:
            frames.append(frame.decode())
        return frames

    frames = asyncio.run(run())
    messages = [json.loads(f[5:])["message"] for f in frames if f.startswith("data:")]
    assert messages == ["repairing", "validating", "done"]
