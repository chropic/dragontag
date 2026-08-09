"""Regression coverage for the shared progress indicator's active-state contract."""
from __future__ import annotations

from pathlib import Path

from dragontag.app import main
from dragontag.app.models import Job, JobStatus


def test_progress_hides_when_only_jobs_are_queued():
    """Waiting jobs are not running work and must not render the global bar."""
    assert main._progress_payload(None, 1) == {
        "active": False, "label": "", "percent": None, "queued": 1,
    }


def test_progress_running_job_has_a_nonblank_label():
    """Legacy task rows with blank names still give the bar useful text."""
    job = Job(source_path="", original_name="   ", kind="scan", status=JobStatus.running)
    payload = main._progress_payload(job, 0)
    assert payload["active"] is True
    assert payload["label"] == "scan — running"
    assert payload["percent"] is None
    assert payload["stoppable"] is True


def test_progress_template_resets_hidden_state_and_settings_clear_it():
    root = Path(__file__).parents[1]
    base = (root / "dragontag/app/web/templates/base.html").read_text(encoding="utf-8")
    settings = (root / "dragontag/app/web/templates/settings.html").read_text(encoding="utf-8")

    assert "if (!this.active)" in base
    assert "d.label || 'Working…'" in base
    assert 'style="top: 5.5rem"' in settings
