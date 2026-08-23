"""INT-009: Grile domain/services must never import Retail implementation code."""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[2] / "src" / "ugrile"
_SCAN_DIRS = (_SOURCE / "domain", _SOURCE / "services")
_FORBIDDEN_ABSOLUTE_ROOTS = {"backend", "services", "repositories", "unihub_retail"}
_FORBIDDEN_TEXT = (
    "anervalens-netizen/unihub-retail",
    "/opt/Mobiup/unihub-retail",
    "grile_pilot_v2_sync",
    "grile_worker_jobs",
)


def test_domain_and_services_do_not_import_retail_implementation() -> None:
    violations: list[str] = []
    for directory in _SCAN_DIRS:
        for path in sorted(directory.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in _FORBIDDEN_ABSOLUTE_ROOTS:
                            violations.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in _FORBIDDEN_ABSOLUTE_ROOTS:
                        violations.append(f"{path.name}: from {node.module}")
            for token in _FORBIDDEN_TEXT:
                if token in source:
                    violations.append(f"{path.name}: contains forbidden Retail token {token!r}")

    assert violations == []
