import json
import logging
import sys
from datetime import UTC, datetime

from app.config import settings

# The attributes every LogRecord has. Anything else on a record came from `extra=` and is a field.
# color_message is uvicorn's copy of the message with terminal colour codes.
_RECORD_ATTRIBUTES = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, which CloudWatch Logs Insights and metric filters read as fields.

    `message` stays the text the code logs, so a filter on a fixed opening such as `jev_fallback`
    works the same on both formats."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRIBUTES and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # default=str: a field given as a UUID or a datetime must not cost the whole line.
        return json.dumps(entry, ensure_ascii=False, default=str)


def _handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return handler


def configure_app_logging() -> None:
    """uvicorn configures only its own loggers, so without this the application's INFO
    lines (the authentication trail, the periodic clean-up) never reach the container
    output, which is where anyone looks first.

    With LOG_FORMAT=json uvicorn's own lines are rewritten too, so that a request log and an
    application log can be queried together."""
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers and not logging.getLogger().handlers:
        app_logger.addHandler(_handler())
    if settings.log_format == "json":
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = [_handler()]
            uvicorn_logger.propagate = False
