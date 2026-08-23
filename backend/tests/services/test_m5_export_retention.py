"""XLSX-004 safe bounded-retention proofs."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ugrile.domain.errors import DomainError
from ugrile.services.export_retention import (
    MANAGED_EXPORT_DIRNAME,
    cleanup_managed_export_artifacts,
)

NOW = datetime(2026, 8, 23, 16, 0, tzinfo=UTC)


def _operation(root: Path, name: str, *, age_hours: int) -> Path:
    path = root / name
    path.mkdir(parents=True)
    artifact = path / "artifact.xlsx"
    artifact.write_bytes(name.encode())
    mtime = (NOW - timedelta(hours=age_hours)).timestamp()
    os.utime(artifact, (mtime, mtime))
    os.utime(path, (mtime, mtime))
    return path


def test_retention_removes_expired_entries_and_never_follows_symlinks(tmp_path: Path) -> None:
    root = tmp_path / MANAGED_EXPORT_DIRNAME
    old = _operation(root, "op-old", age_hours=200)
    fresh = _operation(root, "op-fresh", age_hours=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do-not-delete")
    link = root / "external-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    result = cleanup_managed_export_artifacts(
        base_dir=tmp_path,
        retention_hours=168,
        max_operations=500,
        now=NOW,
    )

    assert result.examined == 2
    assert result.deleted == result.would_delete == 1
    assert result.remaining == 1
    assert result.skipped_symlinks == 1
    assert not old.exists()
    assert fresh.exists()
    assert link.is_symlink()
    assert sentinel.read_text() == "do-not-delete"


def test_retention_count_cap_keeps_newest_operations_deterministically(tmp_path: Path) -> None:
    root = tmp_path / MANAGED_EXPORT_DIRNAME
    oldest = _operation(root, "op-1", age_hours=4)
    middle = _operation(root, "op-2", age_hours=3)
    newest_a = _operation(root, "op-3", age_hours=2)
    newest_b = _operation(root, "op-4", age_hours=1)

    result = cleanup_managed_export_artifacts(
        base_dir=tmp_path,
        retention_hours=999,
        max_operations=2,
        now=NOW,
    )

    assert result.deleted == 2
    assert result.remaining == 2
    assert not oldest.exists()
    assert not middle.exists()
    assert newest_a.exists()
    assert newest_b.exists()


def test_retention_dry_run_reports_without_mutating(tmp_path: Path) -> None:
    root = tmp_path / MANAGED_EXPORT_DIRNAME
    old = _operation(root, "op-old", age_hours=200)

    result = cleanup_managed_export_artifacts(
        base_dir=tmp_path,
        retention_hours=168,
        max_operations=500,
        now=NOW,
        dry_run=True,
    )

    assert result.deleted == 0
    assert result.would_delete == 1
    assert result.remaining == 1
    assert old.exists()


def test_retention_can_protect_current_operation_from_age_and_count_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / MANAGED_EXPORT_DIRNAME
    protected = _operation(root, "op-protected", age_hours=300)
    newer = _operation(root, "op-newer", age_hours=2)

    result = cleanup_managed_export_artifacts(
        base_dir=tmp_path,
        retention_hours=168,
        max_operations=1,
        protected_paths=[protected / "artifact.xlsx"],
        now=NOW,
    )

    assert protected.exists()
    assert not newer.exists()
    assert result.deleted == 1
    assert result.remaining == 1


def test_retention_refuses_symlinked_managed_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside-root"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("safe")
    root = tmp_path / MANAGED_EXPORT_DIRNAME
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(DomainError) as excinfo:
        cleanup_managed_export_artifacts(
            base_dir=tmp_path,
            retention_hours=1,
            max_operations=1,
            now=NOW,
        )

    assert excinfo.value.details["code"] == "EXPORT_RETENTION_ROOT_SYMLINK"
    assert sentinel.read_text() == "safe"
