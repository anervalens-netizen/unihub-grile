"""Bounded retention for locally managed XLSX export artifacts.

Only immediate entries below the managed ``ugrile-s5-exports`` root are ever
removed. The root itself and child symlinks fail/skip closed, so cleanup cannot
escape into operator-managed paths. Two independent limits apply: maximum age
and maximum retained operation entries.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..core.config import get_settings
from ..domain.errors import DomainError

MANAGED_EXPORT_DIRNAME = "ugrile-s5-exports"


@dataclass(frozen=True, slots=True)
class ExportRetentionResult:
    root: str
    examined: int
    deleted: int
    would_delete: int
    remaining: int
    skipped_symlinks: int


@dataclass(frozen=True, slots=True)
class _Entry:
    path: Path
    mtime: float


def managed_export_root(base_dir: str | Path | None = None) -> Path:
    """Resolve the canonical local artifact root used by S5 export paths."""

    base = Path(
        base_dir
        if base_dir is not None
        else (os.environ.get("UGR_S5_EXPORT_DIR") or tempfile.gettempdir())
    ).expanduser()
    return base / MANAGED_EXPORT_DIRNAME


def _protected_top_level_names(root: Path, protected_paths: Iterable[str | Path]) -> set[str]:
    result: set[str] = set()
    resolved_root = root.resolve(strict=False)
    for raw in protected_paths:
        candidate = Path(raw).expanduser().resolve(strict=False)
        try:
            relative = candidate.relative_to(resolved_root)
        except ValueError:
            continue
        if relative.parts:
            result.add(relative.parts[0])
    return result


def _remove_entry(path: Path) -> None:
    if path.is_symlink():
        raise DomainError(
            "refusing to follow export-retention symlink",
            details={"code": "EXPORT_RETENTION_SYMLINK", "path": str(path)},
        )
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def cleanup_managed_export_artifacts(
    *,
    base_dir: str | Path | None = None,
    retention_hours: int | None = None,
    max_operations: int | None = None,
    protected_paths: Iterable[str | Path] = (),
    now: datetime | None = None,
    dry_run: bool = False,
) -> ExportRetentionResult:
    """Apply age + count caps inside the managed export root.

    ``protected_paths`` is available for a future opportunistic/in-process hook;
    any protected path inside the root protects its top-level operation entry.
    The standalone maintenance command does not protect entries and therefore
    enforces the configured count bound exactly.
    """

    settings = get_settings()
    hours = (
        settings.export_artifact_retention_hours
        if retention_hours is None
        else retention_hours
    )
    limit = (
        settings.export_artifact_max_operations
        if max_operations is None
        else max_operations
    )
    if hours < 1 or limit < 1:
        raise DomainError(
            "export retention limits must be positive",
            details={
                "code": "EXPORT_RETENTION_CONFIG_INVALID",
                "retention_hours": hours,
                "max_operations": limit,
            },
        )

    root = managed_export_root(base_dir)
    if root.is_symlink():
        raise DomainError(
            "managed export root must not be a symlink",
            details={"code": "EXPORT_RETENTION_ROOT_SYMLINK", "root": str(root)},
        )
    if not root.exists():
        return ExportRetentionResult(
            root=str(root),
            examined=0,
            deleted=0,
            would_delete=0,
            remaining=0,
            skipped_symlinks=0,
        )
    if not root.is_dir():
        raise DomainError(
            "managed export root is not a directory",
            details={"code": "EXPORT_RETENTION_ROOT_INVALID", "root": str(root)},
        )

    protected_names = _protected_top_level_names(root, protected_paths)
    entries: list[_Entry] = []
    skipped_symlinks = 0
    for child in root.iterdir():
        if child.is_symlink():
            skipped_symlinks += 1
            continue
        stat_result = child.stat()
        entries.append(_Entry(path=child, mtime=stat_result.st_mtime))

    now_value = now or datetime.now(tz=UTC)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=UTC)
    cutoff = (now_value - timedelta(hours=hours)).timestamp()

    selected: dict[str, _Entry] = {}
    for entry in entries:
        if entry.path.name not in protected_names and entry.mtime < cutoff:
            selected[entry.path.name] = entry

    survivors = [entry for entry in entries if entry.path.name not in selected]
    if len(survivors) > limit:
        removable = sorted(
            (
                entry
                for entry in survivors
                if entry.path.name not in protected_names
            ),
            key=lambda entry: (entry.mtime, entry.path.name),
        )
        overflow = len(survivors) - limit
        for entry in removable[:overflow]:
            selected[entry.path.name] = entry

    if not dry_run:
        for entry in sorted(selected.values(), key=lambda item: item.path.name):
            _remove_entry(entry.path)

    would_delete = len(selected)
    deleted = 0 if dry_run else would_delete
    remaining = len(entries) - deleted
    return ExportRetentionResult(
        root=str(root),
        examined=len(entries),
        deleted=deleted,
        would_delete=would_delete,
        remaining=remaining,
        skipped_symlinks=skipped_symlinks,
    )


def main(argv: list[str] | None = None) -> None:
    """Run bounded cleanup as an explicit host maintenance command."""

    parser = argparse.ArgumentParser(description="Clean managed UniHub Grile export artifacts")
    parser.add_argument("--base-dir")
    parser.add_argument("--retention-hours", type=int)
    parser.add_argument("--max-operations", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = cleanup_managed_export_artifacts(
        base_dir=args.base_dir,
        retention_hours=args.retention_hours,
        max_operations=args.max_operations,
        dry_run=args.dry_run,
    )
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":  # pragma: no cover - exercised through service tests
    main()


__all__ = [
    "ExportRetentionResult",
    "MANAGED_EXPORT_DIRNAME",
    "cleanup_managed_export_artifacts",
    "managed_export_root",
]
