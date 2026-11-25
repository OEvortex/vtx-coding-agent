"""The settings picker's recap rows, and the durations they name."""

from ctx.core.config import config
from vx.commands.settings import (
    RECAP_IDLE_CHOICES,
    SettingsCommands,
    recap_idle_label,
    recap_idle_seconds,
)


def rows() -> dict[str, str]:
    command = SettingsCommands()
    command.runtime = None
    return {item.value: item.detail for item in command._settings_rows()}


class TestRecapIdleLabel:
    def test_seconds_read_as_durations(self):
        assert [recap_idle_label(v) for v in (15, 30, 60, 120, 300)] == [
            "15s",
            "30s",
            "1m",
            "2m",
            "5m",
        ]

    def test_every_offered_label_round_trips(self):
        for label in RECAP_IDLE_CHOICES:
            assert recap_idle_label(recap_idle_seconds(label)) == label

    def test_an_unknown_label_keeps_the_default(self):
        assert recap_idle_seconds("whenever") == 60.0

    def test_a_fraction_of_a_minute_is_rounded_not_swallowed(self):
        assert recap_idle_label(90) == "1.5m"


class TestSettingsRows:
    def test_the_recap_can_be_turned_off_from_the_picker(self):
        assert rows()["recap"] == ("on" if config.recap.enabled else "off")

    def test_the_idle_interval_is_shown_as_a_duration(self):
        assert rows()["recap-idle"] == recap_idle_label(config.recap.idle_seconds)
