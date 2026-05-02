"""Octanify — Logging utility.

Provides a unified logger that outputs to Blender console and Python stderr.
"""

import logging
import sys

_LOGGERS: dict[str, logging.Logger] = {}

_FORMAT = "[Octanify] %(levelname)s — %(message)s"


def get_logger(name: str = "octanify") -> logging.Logger:
    """Return a named logger, creating it on first call."""
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)

    _LOGGERS[name] = logger
    return logger
