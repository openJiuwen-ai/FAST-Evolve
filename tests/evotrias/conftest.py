import pytest

from evotrias.utils import io


class _Recorder:
    """Stand-in for ``evotrias.logger.logger`` that keeps every message by level."""

    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def messages(self, level: str) -> list[str]:
        return [msg for lvl, msg in self.records if lvl == level]

    @property
    def warnings(self) -> list[str]:
        return self.messages("warning")

    def debug(self, msg, **kwargs):
        self.records.append(("debug", msg))

    def info(self, msg, **kwargs):
        self.records.append(("info", msg))

    def warning(self, msg, **kwargs):
        self.records.append(("warning", msg))

    def error(self, msg, **kwargs):
        self.records.append(("error", msg))

    def critical(self, msg, **kwargs):
        self.records.append(("critical", msg))


@pytest.fixture
def log(monkeypatch):
    # evotrias.logger writes to ./.evotrias/logs relative to cwd and does not
    # propagate, so swap it for a recorder instead of relying on caplog.
    rec = _Recorder()
    monkeypatch.setattr(io, "logger", rec)
    return rec
