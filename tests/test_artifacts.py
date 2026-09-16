import json
import os
import stat
from unittest import mock

import pytest

from app.ui import artifacts


class TestWriteJsonArtifact:
    def test_writes_timestamped_json(self, tmp_path):
        payload = {"a": 1, "b": ["x", "y"]}
        path = artifacts.write_json_artifact("thing", payload, str(tmp_path))
        assert os.path.basename(path).startswith("thing_")
        assert path.endswith(".json")
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == payload

    def test_creates_missing_directory(self, tmp_path):
        target = tmp_path / "nested" / "dir"
        path = artifacts.write_json_artifact("thing", {"a": 1}, str(target))
        assert os.path.isfile(path)

    def test_unique_paths(self, tmp_path):
        first = artifacts.write_json_artifact("thing", {"a": 1}, str(tmp_path))
        second = artifacts.write_json_artifact("thing", {"a": 2}, str(tmp_path))
        assert first != second

    def test_serializes_non_json_types(self, tmp_path):
        from datetime import date

        path = artifacts.write_json_artifact("thing", {"d": date(2026, 1, 2)}, str(tmp_path))
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == {"d": "2026-01-02"}


class TestRewriteJsonArtifact:
    def test_replaces_contents_in_place(self, tmp_path):
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        artifacts.rewrite_json_artifact(path, {"outcome": "success"})
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == {"outcome": "success"}
        assert len(os.listdir(tmp_path)) == 1

    def test_leaves_no_temp_file_behind(self, tmp_path):
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        artifacts.rewrite_json_artifact(path, {"outcome": "success"})
        assert os.listdir(tmp_path) == [os.path.basename(path)]

    def test_preserves_original_when_write_fails(self, tmp_path):
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        payload: dict = {}
        payload["self"] = payload  # json.dump raises ValueError: Circular reference detected
        with pytest.raises(ValueError, match="Circular reference"):
            artifacts.rewrite_json_artifact(path, payload)
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == {"outcome": "attempted"}
        assert os.listdir(tmp_path) == [os.path.basename(path)]

    def test_preserves_original_when_replace_fails(self, tmp_path):
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        boom = PermissionError("read-only filesystem")
        with mock.patch.object(artifacts.os, "replace", side_effect=boom):
            with pytest.raises(PermissionError, match="read-only filesystem"):
                artifacts.rewrite_json_artifact(path, {"outcome": "success"})
        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == {"outcome": "attempted"}
        assert os.listdir(tmp_path) == [os.path.basename(path)]

    def test_a_failing_cleanup_does_not_mask_the_original_error(self, tmp_path):
        """If a signal lands between ``os.replace`` succeeding and the except block running,
        the temp file is already gone and ``os.unlink`` raises. Cleanup must never outrank
        the failure it is cleaning up after -- the operator needs the real cause.
        """
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        boom = PermissionError("read-only filesystem")
        with (
            mock.patch.object(artifacts.os, "replace", side_effect=boom),
            mock.patch.object(artifacts.os, "unlink", side_effect=FileNotFoundError("already gone")),
        ):
            with pytest.raises(PermissionError, match="read-only filesystem"):
                artifacts.rewrite_json_artifact(path, {"outcome": "success"})

    def test_temp_file_is_written_beside_the_original(self, tmp_path):
        """The atomicity of ``os.replace`` depends on the temp file sharing a filesystem
        with its target, so pin the temp file's location rather than trusting the docstring.
        """
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        seen: list[list[str]] = []

        class DirectoryProbe:
            """Unserializable, so ``default=str`` calls ``str()`` on it mid-dump — while the
            temp file is open and before ``os.replace`` has consumed it."""

            def __str__(self) -> str:
                seen.append(sorted(os.listdir(tmp_path)))
                return "probe"

        artifacts.rewrite_json_artifact(path, {"outcome": DirectoryProbe()})

        assert len(seen) == 1
        during_write = seen[0]
        assert len(during_write) == 2, f"temp file was not created in {tmp_path}: {during_write}"
        assert os.path.basename(path) in during_write
        assert os.listdir(tmp_path) == [os.path.basename(path)]


class TestArtifactPermissions:
    """Artifacts hold PII and rollback records; neither may be world-readable.

    ``data/send_response/`` holds plaintext recipient email addresses and personalisation
    payloads, and ``data/permission_changes/`` holds the pre-change permission set a
    rollback depends on. The writer used to create 0755 directories and 0644 files while
    the rewriter created 0600 via ``mkstemp``, so a record silently changed mode the
    moment its outcome was stamped.
    """

    def test_written_file_is_0600(self, tmp_path):
        path = artifacts.write_json_artifact("thing", {"a": 1}, str(tmp_path))
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_rewritten_file_is_still_0600(self, tmp_path):
        path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(tmp_path))
        artifacts.rewrite_json_artifact(path, {"outcome": "success"})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_created_directory_is_0700(self, tmp_path):
        target = tmp_path / "nested"
        artifacts.write_json_artifact("thing", {"a": 1}, str(target))
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700

    def test_tightens_a_directory_that_already_exists_at_0755(self, tmp_path):
        # makedirs(exist_ok=True) returns silently for an existing directory and will NOT
        # tighten it, so every install created by an earlier version still has a 0755
        # data/permission_changes. The explicit chmod is what fixes those.
        target = tmp_path / "legacy"
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o755

        path = artifacts.write_json_artifact("thing", {"a": 1}, str(target))

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_writes_the_record_even_when_the_directory_cannot_be_tightened(self, tmp_path, caplog):
        """A directory owned by another user -- in practice one created by an earlier
        container run as root over a bind mount -- makes chmod raise. Refusing to write
        would abort the permission change and lose the rollback record, which is worse
        than a loose directory: the file itself is still 0600, so only filenames leak.
        """
        target = tmp_path / "foreign"
        target.mkdir()
        with mock.patch.object(artifacts.os, "chmod", side_effect=PermissionError("not owner")):
            path = artifacts.write_json_artifact("thing", {"outcome": "attempted"}, str(target))

        with open(path, encoding="utf-8") as handle:
            assert json.load(handle) == {"outcome": "attempted"}
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert "Could not tighten" in caplog.text

    def test_refuses_to_overwrite_an_existing_artifact(self, tmp_path):
        # O_EXCL. The timestamp carries microseconds, so a collision means two artifacts
        # in the same microsecond; the previous open(path, "w") would have truncated the
        # first, destroying an audit record with no error. It must raise, not retry.
        fixed = "20260101_000000_000000"
        with mock.patch.object(artifacts, "datetime") as fake_datetime:
            fake_datetime.now.return_value.strftime.return_value = fixed
            first = artifacts.write_json_artifact("thing", {"n": 1}, str(tmp_path))
            with pytest.raises(FileExistsError):
                artifacts.write_json_artifact("thing", {"n": 2}, str(tmp_path))

        with open(first, encoding="utf-8") as handle:
            assert json.load(handle) == {"n": 1}
        assert os.listdir(tmp_path) == [os.path.basename(first)]


class TestDescribeUnwritablePaths:
    def test_writable_directory_reports_nothing(self, tmp_path):
        assert artifacts.describe_unwritable_paths([str(tmp_path)]) == []

    def test_leaves_no_probe_file_behind(self, tmp_path):
        artifacts.describe_unwritable_paths([str(tmp_path)])
        assert os.listdir(tmp_path) == []

    def test_missing_directory_is_fine_when_its_parent_is_writable(self, tmp_path):
        # The writers create directories on demand, so a not-yet-created artifact
        # directory is not a problem -- only an unwritable ancestor is.
        target = tmp_path / "not" / "created" / "yet"
        assert artifacts.describe_unwritable_paths([str(target)]) == []

    def test_unwritable_directory_is_reported_with_owner_and_process_uid(self, tmp_path):
        target = tmp_path / "locked"
        target.mkdir(mode=0o500)
        try:
            problems = artifacts.describe_unwritable_paths([str(target)])
        finally:
            os.chmod(target, 0o700)

        assert len(problems) == 1
        assert str(target) in problems[0]
        assert "owned by uid" in problems[0]
        assert f"uid {os.getuid()}" in problems[0]

    def test_unwritable_ancestor_of_a_missing_directory_is_reported(self, tmp_path):
        parent = tmp_path / "locked"
        parent.mkdir(mode=0o500)
        try:
            problems = artifacts.describe_unwritable_paths([str(parent / "child")])
        finally:
            os.chmod(parent, 0o700)

        assert len(problems) == 1
        assert str(parent) in problems[0]

    def test_a_file_where_a_directory_is_expected_is_reported(self, tmp_path):
        target = tmp_path / "afile"
        target.write_text("not a directory")
        problems = artifacts.describe_unwritable_paths([str(target)])
        assert len(problems) == 1
        assert "is not a directory" in problems[0]

    def test_reports_every_bad_path_not_just_the_first(self, tmp_path):
        good = tmp_path / "good"
        good.mkdir()
        bad1 = tmp_path / "bad1"
        bad1.mkdir(mode=0o500)
        bad2 = tmp_path / "bad2"
        bad2.mkdir(mode=0o500)
        try:
            problems = artifacts.describe_unwritable_paths([str(bad1), str(good), str(bad2)])
        finally:
            os.chmod(bad1, 0o700)
            os.chmod(bad2, 0o700)

        assert len(problems) == 2

    def test_owner_hint_survives_an_unstattable_path(self, tmp_path):
        with mock.patch.object(artifacts.os, "stat", side_effect=OSError("gone")):
            assert "could not stat" in artifacts._describe_owner(str(tmp_path))
