"""Seed the deterministic PostgreSQL dataset used by real-browser CI.

This entry point intentionally reuses the M3 performance fixture instead of
introducing a second browser-only data model. It is test tooling only.
"""

from __future__ import annotations

from tests.fixtures.performance import seed_performance_dataset
from ugrile.core.database import session_scope


def main() -> None:
    with session_scope() as session:
        dataset = seed_performance_dataset(session)
    print(
        "browser-e2e-seed",
        dataset.tenant_id,
        dataset.admin_user_id,
        dataset.month_id,
        len(dataset.store_ids),
        len(dataset.person_ids),
    )


if __name__ == "__main__":
    main()
