import logging


def configure_app_logging() -> None:
    """uvicorn configures only its own loggers, so without this the application's INFO
    lines (the authentication trail, the periodic clean-up) never reach the container
    output, which is where anyone looks first."""
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)
