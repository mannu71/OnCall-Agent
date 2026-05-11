"""Application logging configuration."""
import logging
import sys
from typing import Optional

from app.config import settings


class ColoredFormatter(logging.Formatter):
    """Colored log formatter for console output."""

    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(
    level: Optional[str] = None,
    use_colors: bool = True
) -> None:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to config.LOG_LEVEL or INFO.
        use_colors: Whether to use colored output for console logs.
    """
    log_level = (level or getattr(settings, "LOG_LEVEL", "INFO")).upper()

    # Remove existing handlers to avoid duplicates
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Console handler with optional colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    if use_colors:
        formatter: logging.Formatter = ColoredFormatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")
    else:
        formatter = logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")
    console_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.setLevel(log_level)

    # Reduce noise from third-party libraries
    for noisy_logger in ("httpx", "httpcore", "botocore", "boto3"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)