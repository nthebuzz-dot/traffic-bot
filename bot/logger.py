import logging
import sys


def setup_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    Return a logger for *name*.

    Handlers are added only once per logger instance so that calling
    setup_logger() at module level in multiple files does not cause
    duplicate log lines.

    Args:
        name:     Logger name (typically __name__).
        log_file: Optional path to a log file.

    Returns:
        Configured Logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers when the module is imported more than once
    # (e.g. during testing or when the dashboard reloads).
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Console handler — INFO and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Optional file handler — DEBUG and above
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Prevent log records from bubbling up to the root logger
    # (avoids double-printing when the dashboard attaches its own root handler)
    logger.propagate = False

    return logger
