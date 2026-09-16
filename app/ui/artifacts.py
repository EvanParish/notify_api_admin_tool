"""Timestamped JSON artifact persistence.

Deliberately imports no NiceGUI so callers that must stay unit-testable (for example
``app.ui.permission_helpers``) can use it without pulling the UI framework in.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger(__name__)

# Artifacts are private to the operator who produced them. data/send_response/ holds
# plaintext recipient email addresses and personalisation payloads -- PII in a VA Notify
# context -- and data/permission_changes/ holds the rollback record for a destructive
# production change. Neither belongs in a world-readable file on a shared or
# multi-account workstation. rewrite_json_artifact already produced 0600 via mkstemp;
# these constants make the writer match rather than leaving one 65-line module with two
# different answers.
# Default artifact directories. SEND_RESULTS_DIR lives here rather than in helpers.py
# because app.ui.state needs it for the startup writability check, and helpers.py imports
# from state -- putting it there would be an import cycle.
SEND_RESULTS_DIR = "data/send_response"

ARTIFACT_DIR_MODE = 0o700
ARTIFACT_FILE_MODE = 0o600


def _dump(payload: dict[str, Any], handle: TextIO) -> None:
    """Serialize *payload* with the options every artifact must share.

    Both writers go through here: :func:`rewrite_json_artifact` is defined in terms of
    files produced by :func:`write_json_artifact`, so the two must never diverge in
    format. ``default=str`` keeps non-JSON values (datetimes, UUIDs) from raising.
    """
    json.dump(payload, handle, indent=2, default=str)


def write_json_artifact(prefix: str, payload: dict[str, Any], directory: str) -> str:
    """Write *payload* to ``{directory}/{prefix}_{utc timestamp}.json``.

    The directory is forced to 0700 and the file created 0600.

    The explicit ``chmod`` is not redundant with the ``makedirs`` mode: ``exist_ok=True``
    returns silently for a directory that already exists and will NOT tighten it, so every
    install that ran an earlier version still has a 0755 ``data/permission_changes`` and
    ``data/send_response``. Note the mode applies to the leaf only -- intermediate parents
    created by ``makedirs`` keep the process umask, which is correct: ``data/`` itself is
    not the thing holding PII.

    ``O_EXCL`` makes the create fail rather than truncate if the path already exists. The
    timestamp carries microseconds, so a collision means two artifacts were produced in
    the same microsecond; the old code would have silently overwritten the first, losing
    an audit record. It raises ``FileExistsError`` deliberately, with no retry: a lost
    rollback record must be loud, and a retry loop here would be untested code guarding an
    event that should never happen.

    Returns the path of the written file.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    os.makedirs(directory, mode=ARTIFACT_DIR_MODE, exist_ok=True)
    # Best-effort, not fatal. chmod raises PermissionError when the directory already
    # exists and is owned by a different user -- the common case being a directory created
    # by an earlier container run as root over a bind mount. Refusing to write the audit
    # record in that situation would abort the permission change entirely, which is a worse
    # outcome than a loose directory: the file itself is created 0600 below, so its CONTENTS
    # are protected regardless. A permissive directory only exposes filenames.
    try:
        os.chmod(directory, ARTIFACT_DIR_MODE)
    except OSError:
        logger.warning(
            "Could not tighten %s to %o; artifact contents are still written 0600, "
            "but the directory is readable by other local users.",
            directory,
            ARTIFACT_DIR_MODE,
        )
    file_path = os.path.join(directory, f"{prefix}_{timestamp}.json")
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, ARTIFACT_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        _dump(payload, handle)
    return file_path


def rewrite_json_artifact(file_path: str, payload: dict[str, Any]) -> None:
    """Atomically replace the contents of an artifact written by :func:`write_json_artifact`.

    Used to stamp a final outcome onto a record that was deliberately written before the
    risky operation it describes, so the record survives a crash mid-operation. Writing in
    place would defeat that: truncating the file and then failing mid-write would destroy
    the very record we are trying to preserve. Instead the new contents go to a temporary
    file in the same directory — same filesystem, so :func:`os.replace` is atomic — which
    then replaces the original in a single step. A failure anywhere leaves the original
    untouched and removes the temporary file.

    *file_path* must be a path returned by :func:`write_json_artifact`. This is not
    checked: ``os.replace`` happily creates a missing target, so a stale or wrong path
    silently writes a second artifact while the real record stays stamped as attempted —
    two files, neither correct, and no error raised.

    ``mkstemp`` creates at 0600 and ``os.replace`` carries that mode onto the target, so
    the rewritten record keeps the same permissions :func:`write_json_artifact` gave it.
    """
    directory = os.path.dirname(file_path) or "."
    handle_fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            _dump(payload, handle)
        os.replace(tmp_path, file_path)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt or SystemExit mid-write must
        # still remove the temp file. A plain finally would unlink after a successful replace.
        #
        # suppress(OSError): if a signal lands between os.replace succeeding and this block
        # exiting, tmp_path is already gone and the unlink would raise FileNotFoundError,
        # masking the real exception. Cleanup must never outrank the failure it is cleaning
        # up after.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def describe_unwritable_paths(paths: Sequence[str]) -> list[str]:
    """Return a human-readable problem per path in *paths* that cannot be written to.

    Returns an empty list when every path is usable. Each path is treated as a directory
    the app needs to create files in; a path that does not exist yet is fine as long as
    its nearest existing ancestor is writable, because the writers create directories on
    demand.

    This exists because the failures it catches are otherwise diagnosed from a traceback
    thrown minutes after startup, in the middle of an operation. Two separate incidents
    in this codebase had the same root cause -- a bind-mounted directory owned by root
    because the Docker daemon or an earlier root container created it, against a container
    now running as the host uid -- and both surfaced as ``PermissionError`` from unrelated
    library code. A named, up-front check turns that into one line.

    The check creates and deletes a real temporary file rather than calling ``os.access``.
    For the failure this was written for -- a root-owned 0700 directory against a process
    running as the host uid -- the two agree, and no test here distinguishes them. The
    create is kept because it also covers read-only mounts and ACLs, where ``os.access``
    consults only the classic permission bits; treat that as a deliberate margin rather
    than a demonstrated one.
    """
    problems: list[str] = []
    for path in paths:
        target = Path(path).absolute()
        # Nearest existing ancestor, including the path itself. The writers create
        # directories on demand, so a not-yet-created artifact directory is fine as long
        # as something above it is writable. ``parents`` terminates at the filesystem
        # root, which always exists, so the fallback is unreachable -- expressed as a
        # default rather than a branch so it cannot show up as an uncoverable line.
        probe_dir = next((p for p in (target, *target.parents) if p.exists()), target)
        if not probe_dir.is_dir():
            problems.append(f"{target}: {probe_dir} exists but is not a directory")
            continue
        try:
            fd, probe = tempfile.mkstemp(dir=probe_dir, prefix=".writecheck-")
        except OSError as exc:
            owner = _describe_owner(probe_dir)
            problems.append(f"{target}: cannot create files in {probe_dir} ({exc.strerror}); {owner}")
            continue
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(probe)
    return problems


def _describe_owner(path: Path) -> str:
    """Best-effort 'owned by uid N, running as uid M' hint for a permission failure."""
    try:
        info = os.stat(path)
    except OSError:
        return "could not stat it to report its owner"
    return f"it is owned by uid {info.st_uid}:gid {info.st_gid} and this process runs as uid {os.getuid()}:gid {os.getgid()}"
