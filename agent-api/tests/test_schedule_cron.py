"""Unit tests for Schedule-node cron conversion helpers.

Covers ``_days_to_cron_dow`` and ``_params_to_cron`` in
``app.api.v1.endpoints.workflows``.

``_params_to_cron`` reads the operator-configured GLOBAL timezone via
``app.core.app_timezone.get_global_timezone_name`` (imported *inside* the
function). To make local→UTC conversion deterministic we monkeypatch that name
at ``app.core.app_timezone.get_global_timezone_name`` (the source module), which
is where the function-local ``from ... import ...`` resolves it.
"""
import pytest

from app.api.v1.endpoints.workflows import _days_to_cron_dow, _params_to_cron


# --------------------------------------------------------------------------- #
# _days_to_cron_dow
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "days, expected",
    [
        ("Mon,Tue,Wed,Thu,Fri", "1,2,3,4,5"),
        ("Sat,Sun", "6,0"),
        ("", "*"),                              # empty → every day
        ("Mon,Tue,Wed,Thu,Fri,Sat,Sun", "*"),   # all seven → every day
        ("Wed,Mon", "1,3"),                      # order-independent (Mon→Sun)
        ("Mon,Bogus,Fri", "1,5"),                # unknown tokens ignored
    ],
)
def test_days_to_cron_dow(days, expected):
    assert _days_to_cron_dow(days) == expected


# --------------------------------------------------------------------------- #
# _params_to_cron — deterministic via UTC global tz
# --------------------------------------------------------------------------- #
@pytest.fixture
def utc_tz(monkeypatch):
    """Force the global timezone to UTC so local time == UTC time."""
    monkeypatch.setattr(
        "app.core.app_timezone.get_global_timezone_name",
        lambda: "UTC",
    )


@pytest.mark.parametrize(
    "params, expected",
    [
        # Fixed-interval frequencies (no time conversion)
        ({"frequency": "Every 5 min"}, "*/5 * * * *"),
        ({"frequency": "Every 15 min"}, "*/15 * * * *"),
        ({"frequency": "Every 30 min"}, "*/30 * * * *"),
        ({"frequency": "Hourly"}, "0 * * * *"),
        # Daily with explicit weekdays
        (
            {"frequency": "Daily", "time": "09:00", "days": "Mon,Tue,Wed,Thu,Fri"},
            "0 9 * * 1,2,3,4,5",
        ),
        # Daily with no days → every day
        ({"frequency": "Daily", "time": "09:00", "days": ""}, "0 9 * * *"),
        # Weekly with explicit weekend days
        ({"frequency": "Weekly", "time": "18:30", "days": "Sat,Sun"}, "30 18 * * 6,0"),
        # Weekly with no days → defaults to Monday
        ({"frequency": "Weekly", "time": "18:30", "days": ""}, "30 18 * * 1"),
        # Monthly → 1st of the month
        ({"frequency": "Monthly", "time": "00:00"}, "0 0 1 * *"),
    ],
)
def test_params_to_cron_utc(utc_tz, params, expected):
    assert _params_to_cron(params) == expected


def test_params_to_cron_uses_global_tz_for_conversion(monkeypatch):
    """Non-UTC global tz must shift the cron hour into UTC.

    On 2024-01-15 (the fixed reference date used internally) America/New_York is
    EST = UTC-5. A Daily schedule at 09:00 local therefore maps to 14:00 UTC.
    """
    monkeypatch.setattr(
        "app.core.app_timezone.get_global_timezone_name",
        lambda: "America/New_York",
    )
    params = {"frequency": "Daily", "time": "09:00", "days": ""}
    assert _params_to_cron(params) == "0 14 * * *"
