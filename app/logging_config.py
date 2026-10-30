"""structlog configuration producing one JSON object per log line.

JSON output is deliberate: it is trivially ingestible by log shippers and keeps
the request ID available as a structured field rather than a substring.
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib logging and structlog for JSON output at ``level``."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        # Re-resolve the logger factory on each call so a later reconfigure (or a
        # test capturing stdout) takes effect immediately.
        cache_logger_on_first_use=False,
    )
