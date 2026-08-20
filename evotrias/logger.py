from __future__ import annotations

import logging
import os
import tarfile
import traceback
from datetime import datetime, timezone

from evotrias.utils.filelock import blocking_filelock

_LOGGER_NAME = "evotrias"
_LOG_DIR = os.path.join(".", ".evotrias", "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "latest.log")
_LOCK_PATH = os.path.join(".", ".evotrias", "logs.lock")
_ROTATE_BYTES = 1 * 1024 * 1024
_FORMAT = "%(asctime)s %(levelname)s <%(process)d> %(filename)s:%(lineno)d[%(funcName)s] %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


class _RotatingFileHandler(logging.Handler):

    @staticmethod
    def _rotate() -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archived = os.path.join(_LOG_DIR, f"{ts}.log")
        counter = 0
        # A prior rotation this same second leaves either [ts].log.tar.gz
        # (tar succeeded) or [ts].log (tar failed); neither may be clobbered.
        while os.path.exists(f"{archived}.tar.gz") or os.path.exists(archived):
            counter += 1
            archived = os.path.join(_LOG_DIR, f"{ts}.{counter}.log")
        os.rename(_LOG_PATH, archived)
        with tarfile.open(f"{archived}.tar.gz", "w:gz") as tar:
            tar.add(archived, arcname=os.path.basename(archived))
        os.remove(archived)
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record) + "\n"
            os.makedirs(_LOG_DIR, exist_ok=True)
            with blocking_filelock(_LOCK_PATH, timeout=5.0) as held:
                # held=False only after timeout: better an unguarded write
                # than a lost line; rotation is skipped, it has fallbacks.
                with open(_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(msg)
                    size = f.tell()
                if held and size >= _ROTATE_BYTES:
                    self._rotate()
        except Exception:
            # A logger must never crash its caller: leave a breadcrumb by
            # writing the file directly (logging here would recurse into
            # emit); if even that fails, the failure is dropped for good.
            try:
                ts = datetime.now().astimezone().strftime(_DATEFMT)
                line = f"{ts} ERROR <{os.getpid()}> LOGGER EMIT FAILED:\n{traceback.format_exc()}"
                with blocking_filelock(_LOCK_PATH, timeout=1.0):
                    with open(_LOG_PATH, "a", encoding="utf-8") as f:
                        f.write(line)
            except Exception:
                pass


class _Logger:
    @staticmethod
    def _configured_level() -> int:
        raw = os.environ.get("EVOTRIAS_LOG_LEVEL", "INFO").upper()
        return getattr(logging, raw, logging.INFO)

    def __init__(self) -> None:
        self._log = logging.getLogger(_LOGGER_NAME)
        self._log.setLevel(self._configured_level())
        self._log.propagate = False
        self._log.handlers.clear()
        handler = _RotatingFileHandler()
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        handler.setLevel(logging.DEBUG)
        self._log.addHandler(handler)

    def debug(self, msg: str, stacklevel: int = 2, **kwargs) -> None:
        self._log.debug(msg, stacklevel=stacklevel, **kwargs)

    def info(self, msg: str,  stacklevel: int = 2, **kwargs) -> None:
        self._log.info(msg, stacklevel=stacklevel, **kwargs)

    def warning(self, msg: str,   stacklevel: int = 2, **kwargs) -> None:
        self._log.warning(msg, stacklevel=stacklevel, **kwargs)

    def error(self, msg: str, stacklevel: int = 2, **kwargs) -> None:
        self._log.error(msg, stacklevel=stacklevel, **kwargs)

    def critical(self, msg: str,  stacklevel: int = 2, **kwargs) -> None:
        self._log.critical(msg, stacklevel=stacklevel, **kwargs)


logger = _Logger()
