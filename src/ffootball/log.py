"""
Structured logging for ffootball.

Writes to data/agent.log. Console output stays clean by default — only
the file handler is attached unless verbose mode is enabled.
"""
import logging
import os
from pathlib import Path


def setup_logging(db_path: Path | None = None, verbose: bool = False) -> None:
    """
    Configure root logger. Call once at startup.
    Writes DEBUG+ to data/agent.log, WARNING+ to stderr if verbose.
    """
    root = logging.getLogger("ffootball")
    if root.handlers:
        return  # already configured

    root.setLevel(logging.DEBUG)

    # File handler — always on, writes to data/ next to DB
    log_dir = db_path.parent if db_path else Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "agent.log"

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root.addHandler(fh)

    # Console handler — only warnings unless verbose
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ffootball.{name}")
