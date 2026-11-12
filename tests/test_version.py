"""Tests for app/version.py and the release metadata it reads."""

import re
import tomllib

import pytest

from app.version import (
    CHANGELOG_PATH,
    PROJECT_ROOT,
    PYPROJECT_PATH,
    UNKNOWN_VERSION,
    get_release_notes,
    get_version,
)

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@pytest.fixture(autouse=True)
def clear_version_caches():
    get_version.cache_clear()
    get_release_notes.cache_clear()
    yield
    get_version.cache_clear()
    get_release_notes.cache_clear()


def test_get_version_reads_pyproject():
    with PYPROJECT_PATH.open("rb") as fh:
        expected = tomllib.load(fh)["project"]["version"]
    assert get_version() == expected
    assert SEMVER.match(get_version())


def test_get_version_is_cached(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nversion = "2.3.4"\n')
    assert get_version(path) == "2.3.4"
    path.write_text('[project]\nversion = "9.9.9"\n')
    assert get_version(path) == "2.3.4"


@pytest.mark.parametrize(
    "content",
    [
        None,  # missing file
        "not = [valid toml",
        '[tool.other]\nversion = "1.0.0"\n',  # no [project] table
        'project = "a string, not a table"\n',
    ],
)
def test_get_version_falls_back_to_unknown(tmp_path, caplog, content):
    path = tmp_path / "pyproject.toml"
    if content is not None:
        path.write_text(content)
    assert get_version(path) == UNKNOWN_VERSION
    assert "Could not read app version" in caplog.text


def test_get_release_notes_drops_preamble(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nPreamble for contributors.\n\n## [1.1.0] - 2026-12-01\n\n- Thing\n\n")
    assert get_release_notes(path) == "## [1.1.0] - 2026-12-01\n\n- Thing\n"


def test_get_release_notes_without_sections(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\nNothing released yet.\n")
    assert get_release_notes(path) == ""


def test_get_release_notes_missing_file(tmp_path, caplog):
    assert get_release_notes(tmp_path / "missing.md") == ""
    assert "Could not read changelog" in caplog.text


def test_changelog_has_section_for_current_version():
    """The release workflow publishes this section as the GitHub release notes."""
    current = get_version()
    headings = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG_PATH.read_text(encoding="utf-8"), re.MULTILINE)
    assert headings, "CHANGELOG.md has no release sections"
    assert headings[0] == current, f"newest CHANGELOG.md section is {headings[0]}, pyproject.toml says {current}"


def test_docker_image_includes_version_files():
    """Without these files the image would show 'vunknown' and an empty What's New page."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied = " ".join(line for line in dockerfile.splitlines() if line.startswith("COPY "))
    for name in (PYPROJECT_PATH.name, CHANGELOG_PATH.name):
        assert name in copied
