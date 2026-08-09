"""The shared limiter spaces starts without serializing response duration."""
import threading
import time

from fastapi.testclient import TestClient

from dragontag.app import tasks
from dragontag.app.db import session
from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.identify.musicbrainz import RequestStartGate
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus


def test_second_request_starts_on_slot_while_first_response_is_blocked():
    gate = RequestStartGate(interval=0.05)
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def request(number: int):
        gate.wait()
        if number == 1:
            first_started.set()
            release_first.wait(1)
        else:
            second_started.set()

    first = threading.Thread(target=request, args=(1,))
    second = threading.Thread(target=request, args=(2,))
    first.start()
    assert first_started.wait(0.2)
    second.start()
    assert second_started.wait(0.3)
    assert first.is_alive(), "second start incorrectly waited for first network response"
    release_first.set()
    first.join(1)
    second.join(1)
    assert not first.is_alive() and not second.is_alive()


def test_gate_spaces_request_starts():
    gate = RequestStartGate(interval=0.04)
    starts = []
    for _ in range(2):
        gate.wait()
        starts.append(time.monotonic())
    assert starts[1] - starts[0] >= 0.035


def test_blocked_lookup_does_not_block_authenticated_route_or_independent_task(monkeypatch):
    lookup_started = threading.Event()
    release_lookup = threading.Event()
    task_finished = threading.Event()

    def blocked_search(**kwargs):
        lookup_started.set()
        release_lookup.wait(1)
        return {"recording-list": []}

    monkeypatch.setattr(mbq, "_ensure_configured", lambda: None)
    monkeypatch.setattr(mbq.mb, "search_recordings", blocked_search)
    lookup_thread = threading.Thread(
        target=lambda: mbq.search_candidates(
            title="blocked", artist=None, album=None, raise_on_error=True
        )
    )
    lookup_thread.start()
    assert lookup_started.wait(0.2)
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app, follow_redirects=False)
        started = time.monotonic()
        response = client.get("/settings")
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert elapsed < 0.5
        task_id = tasks.run_task(
            "responsiveness_probe", "independent probe", lambda ctx: task_finished.set()
        )
        assert task_finished.wait(0.5)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with session() as s:
                if s.get(Job, task_id).status == JobStatus.done:
                    break
            time.sleep(0.01)
        else:
            raise AssertionError("independent tracked task did not finish its database commit")
        assert lookup_thread.is_alive()
    finally:
        app.dependency_overrides.pop(require_auth, None)
        release_lookup.set()
        lookup_thread.join(1)
