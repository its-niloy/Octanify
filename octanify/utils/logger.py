"""Octanify — Logging utility.

Provides a unified logger that outputs to Blender console and Python stderr.
"""

import logging
import sys

_LOGGERS: dict[str, logging.Logger] = {}

_FORMAT = "[Octanify] %(levelname)s — %(message)s"


class _EncodingSafeStreamHandler(logging.StreamHandler):
    """Render Unicode safely in Blender and legacy Windows consoles."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            encoding = getattr(self.stream, "encoding", None)
            if encoding:
                message = message.encode(encoding, errors="replace").decode(encoding)
            self.stream.write(message + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


def get_logger(name: str = "octanify") -> logging.Logger:
    """Return a named logger, creating it on first call."""
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        handler = _EncodingSafeStreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)

    _LOGGERS[name] = logger
    return logger
