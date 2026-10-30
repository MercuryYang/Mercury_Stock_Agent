import logging
import re

import structlog

SENSITIVE = re.compile(r"token|secret|password|authorization|api.?key|account.?number|hash.?value", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def redact_processor(logger, method_name, event_dict):
    return redact(event_dict)


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Request URLs / exception bodies can contain brokerage identifiers or OAuth codes.
    for name in ("httpx", "httpcore", "openai", "openai.agents", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.JSONRenderer(),
        ]
    )
