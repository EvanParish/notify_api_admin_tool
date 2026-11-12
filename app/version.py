"""App version and changelog, read from files at the project root.

The project is not installed as a package (`[tool.uv] package = false`), so
`importlib.metadata` cannot see it. `pyproject.toml` is the single source of truth
for the version, and the Docker image copies it alongside `CHANGELOG.md`.
"""

from __future__ import annotations

import logging
import tomllib
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
UNKNOWN_VERSION = "unknown"
RELEASES_URL = "https://github.com/EvanParish/notify_api_admin_tool/releases"


@cache
def get_version(path: Path = PYPROJECT_PATH) -> str:
    """Return the `[project] version` from pyproject.toml, or "unknown" if it can't be read."""
    try:
        with path.open("rb") as fh:
            version = tomllib.load(fh)["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        logger.warning("Could not read app version from %s: %s", path, exc)
        return UNKNOWN_VERSION
    return str(version)


@cache
def get_release_notes(path: Path = CHANGELOG_PATH) -> str:
    """Return the changelog's release sections (everything from the first `## ` heading).

    The file's title and contributor-facing preamble are dropped; the page supplies its own.
    Returns "" if the file is missing or has no release sections.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read changelog from %s: %s", path, exc)
        return ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[index:]).strip() + "\n"
    return ""
