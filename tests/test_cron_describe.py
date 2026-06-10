"""scheduler.describe_cron and the cron-describe endpoint contract."""
from dragontag.app import scheduler


def test_describe_valid_weekly():
    desc = scheduler.describe_cron("0 6 * * 2")
    assert desc and "06:00" in desc and "Tuesday" in desc


def test_describe_weekdays():
    desc = scheduler.describe_cron("0 12 * * 1-5")
    assert desc and "Monday" in desc and "Friday" in desc


def test_describe_invalid_returns_none():
    assert scheduler.describe_cron("not a cron") is None
    assert scheduler.describe_cron("") is None


def test_batch_task_types_registered():
    assert "batch_organize" in scheduler.TASK_TYPES
    assert "batch_retag" in scheduler.TASK_TYPES
