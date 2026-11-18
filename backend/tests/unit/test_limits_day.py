from datetime import UTC, date, datetime

from app.generation import limits


class _Clock(datetime):
    """datetime.now at a fixed instant, in whatever zone it is asked for."""
    instant = datetime(2026, 11, 18, 17, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


def test_the_day_turns_at_midnight_in_vietnam_not_in_utc(monkeypatch):
    monkeypatch.setattr(limits, "datetime", _Clock)
    # 17:30 UTC is 00:30 the next day in Vietnam: a limit that ran out at 23:00 there is back.
    assert limits.today() == date(2026, 11, 19)
    _Clock.instant = datetime(2026, 11, 18, 16, 59, tzinfo=UTC)
    assert limits.today() == date(2026, 11, 18)
