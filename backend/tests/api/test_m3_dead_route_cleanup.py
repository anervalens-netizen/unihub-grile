from __future__ import annotations

from ugrile.main import create_app


def test_calendar_write_surface_has_no_legacy_duplicate_routes() -> None:
    """Only Program cell and signed XLSX apply remain calendar write APIs."""

    paths = create_app().openapi()["paths"]

    assert "post" not in paths["/months/{month_id}/assignments"]
    assert "/months/{month_id}/calendar/apply" not in paths
    assert "post" in paths["/months/{month_id}/program/cell"]
    assert "post" in paths["/months/{month_id}/schedule/apply"]

    # Read/diagnostic compatibility remains intentionally available.
    assert "get" in paths["/months/{month_id}/assignments"]
    assert "get" in paths["/months/{month_id}/coverage"]
