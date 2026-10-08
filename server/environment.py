from __future__ import annotations

from os import PathLike
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_environment(path: str | PathLike[str] | None = None) -> bool:
    """Load local configuration without overriding injected environment values."""
    dotenv_path = Path(path) if path is not None else PROJECT_ROOT / ".env"
    return load_dotenv(dotenv_path=dotenv_path, override=False)


__all__ = ["PROJECT_ROOT", "load_environment"]
