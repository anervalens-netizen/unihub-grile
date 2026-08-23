from __future__ import annotations

from ugrile.core.metrics import observe_close_blockers


def test_metrics_are_operational_and_do_not_expose_business_identifiers(client, faker_tenant) -> None:
    # Populate at least one route-template observation before scraping.
    response = client.get(
        "/version?email=alice@example.test&salary=9999",
        headers={"X-Correlation-ID": "metrics-private-correlation"},
    )
    assert response.status_code == 200
    observe_close_blockers(3)

    metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    text = metrics.text
    assert "ugrile_http_requests_total" in text
    assert 'route="/version"' in text
    assert "ugrile_jobs_current" in text
    assert "ugrile_job_retry_backlog" in text
    assert "ugrile_sheet_projection_last_success_age_seconds" in text
    assert "ugrile_close_blockers_last_observed 3" in text
    assert "ugrile_close_blocker_observations_total" in text

    forbidden = [
        faker_tenant["tenant_id"],
        faker_tenant["store_id"],
        faker_tenant["person_a_id"],
        "alice@example.test",
        "salary=9999",
        "metrics-private-correlation",
    ]
    for marker in forbidden:
        assert marker not in text
