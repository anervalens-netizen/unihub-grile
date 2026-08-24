from __future__ import annotations

from pathlib import Path

from pydantic.fields import FieldInfo

from ugrile.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _settings_env_name(name: str, field: FieldInfo) -> str:
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias
    return name.upper()


def test_environment_inventory_covers_every_backend_setting() -> None:
    inventory = (ROOT / "docs/operations/environment.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    names = {
        _settings_env_name(name, field)
        for name, field in Settings.model_fields.items()
    }
    for name in sorted(names):
        assert f"`{name}`" in inventory, name
        assert name in example, name

    direct_runtime_names = {
        "UGRILE_HOST",
        "UGRILE_PORT",
        "UGR_S5_EXPORT_DIR",
        "UGR_S5_GOOGLE_FAIL",
        "UGRILE_API_PROXY",
        "UGRILE_PG_URL",
    }
    for name in direct_runtime_names:
        assert f"`{name}`" in inventory, name
        assert name in example, name


def test_backup_restore_runbook_is_restore_first_and_fail_closed() -> None:
    text = (ROOT / "docs/operations/backup-restore-migrations.md").read_text(
        encoding="utf-8"
    )

    required = [
        "pg_dump --format=custom",
        "sha256sum",
        "pg_restore --list",
        "--exit-on-error",
        "separate",
        ".venv/bin/alembic current",
        ".venv/bin/alembic check",
        ".venv/bin/alembic upgrade head",
        "Do **not** run `.venv/bin/alembic downgrade`",
        "/livez",
        "/readyz",
        "UniHub Retail",
    ]
    for marker in required:
        assert marker in text, marker

    # The documented restore target must be distinct from the active DB rather
    # than an in-place destructive recipe.
    assert 'RESTORE_TEST_DATABASE_URL' in text
    assert "never the active primary URL" in text
