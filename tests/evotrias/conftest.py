import pytest

from evotrias.utils import io


class _Recorder:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg, **kwargs):
        self.warnings.append(msg)

    def debug(self, msg, **kwargs):
        pass

    info = error = critical = debug


@pytest.fixture
def log(monkeypatch):
    # evotrias.logger writes to ./.evotrias/logs relative to cwd and does not
    # propagate, so swap it for a recorder instead of relying on caplog.
    rec = _Recorder()
    monkeypatch.setattr(io, "logger", rec)
    return rec
