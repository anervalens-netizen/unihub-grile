"""SEC-011 explicit catalog capability enforcement."""

from __future__ import annotations

import pytest

from ugrile.domain.enums import RoleName
from ugrile.services import authorization
from ugrile.services.authorization import Capability

HEADERS = {
    "X-Ugrile-Identity": "user_admin",
    "X-Ugrile-Tenant": "tenant_acme",
}


@pytest.mark.parametrize("path", ["/catalog/stores", "/catalog/people"])
def test_catalog_reads_fail_closed_when_catalog_capability_is_removed(
    client,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Route authorization must not rely on today's fixed role policy alone."""

    admin_capabilities = authorization._ROLE_CAPABILITIES[RoleName.ADMIN]
    monkeypatch.setitem(
        authorization._ROLE_CAPABILITIES,
        RoleName.ADMIN,
        frozenset(capability for capability in admin_capabilities if capability is not Capability.CATALOG_READ),
    )

    response = client.get(path, headers=HEADERS)

    assert response.status_code == 403
    assert response.json() == {
        "code": "FORBIDDEN",
        "message": "principal does not have the required capability",
        "details": {
            "principal": "user_admin",
            "role": "ADMIN",
            "capability": "catalog.read",
        },
    }
