"""H3: reap_stale_jobs converts heartbeat-stale ``running`` jobs to ``error``.

A task that hangs or dies silently would otherwise stay ``running`` forever,
wedging same-kind scheduling. Healthy long tasks heartbeat (bump updated_at),
so only stalled ones trip the reaper.
"""
from dragontag.app import tasks
from dragontag.app.db import session
from dragontag.app.models import Job, JobStatus
from dragontag.app.timeutil import now_utc


def _make_running(updated_delta) -> int:
    with session() as s:
        j = Job(source_path="", original_name="x", kind="scan", status=JobStatus.running)
        j.updated_at = now_utc() - updated_delta
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def test_stale_running_job_is_reaped():
    jid = _make_running(tasks.STALE_RUNNING_AFTER + __import__("datetime").timedelta(minutes=1))
    reaped = tasks.reap_stale_jobs()
    assert reaped >= 1
    with session() as s:
        j = s.get(Job, jid)
        assert j.status == JobStatus.error
        assert "stalled task" in (j.error or "")


def test_fresh_running_job_is_left_alone():
    import datetime
    jid = _make_running(datetime.timedelta(seconds=1))
    tasks.reap_stale_jobs()
    with session() as s:
        j = s.get(Job, jid)
        assert j.status == JobStatus.running
