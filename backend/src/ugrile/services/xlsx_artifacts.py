"""Deterministic XLSX/ZIP serialization primitives.

OpenPyXL updates ``docProps/core.xml`` and ZIP member timestamps when saving.
Those transport-level timestamps make identical logical workbooks hash
differently across retries. Durable export checksums therefore canonicalize both
the OOXML core timestamps and every ZIP member timestamp/order.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable

from openpyxl import Workbook

_CANONICAL_W3CDTF = b"2000-01-01T00:00:00Z"
_CANONICAL_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_CORE_CREATED_RE = re.compile(
    br"(<dcterms:created\b[^>]*>)[^<]*(</dcterms:created>)"
)
_CORE_MODIFIED_RE = re.compile(
    br"(<dcterms:modified\b[^>]*>)[^<]*(</dcterms:modified>)"
)


def _canonical_core_properties(payload: bytes) -> bytes:
    payload = _CORE_CREATED_RE.sub(
        br"\g<1>" + _CANONICAL_W3CDTF + br"\g<2>",
        payload,
    )
    return _CORE_MODIFIED_RE.sub(
        br"\g<1>" + _CANONICAL_W3CDTF + br"\g<2>",
        payload,
    )


def deterministic_zip(entries: Iterable[tuple[str, bytes]]) -> bytes:
    """Build a byte-stable ZIP from named byte payloads."""

    normalized = sorted(((str(name), bytes(data)) for name, data in entries), key=lambda x: x[0])
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        for name, data in normalized:
            info = zipfile.ZipInfo(filename=name, date_time=_CANONICAL_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(
                info,
                data,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return output.getvalue()


def canonicalize_ooxml_zip(payload: bytes) -> bytes:
    """Canonicalize an existing OOXML ZIP without changing workbook semantics."""

    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as source:
        entries: list[tuple[str, bytes]] = []
        for member in source.infolist():
            data = source.read(member.filename)
            if member.filename == "docProps/core.xml":
                data = _canonical_core_properties(data)
            entries.append((member.filename, data))
    return deterministic_zip(entries)


def save_workbook_deterministic(workbook: Workbook) -> bytes:
    """Serialize one OpenPyXL workbook to stable bytes/checksum material."""

    raw = io.BytesIO()
    workbook.save(raw)
    return canonicalize_ooxml_zip(raw.getvalue())


__all__ = [
    "canonicalize_ooxml_zip",
    "deterministic_zip",
    "save_workbook_deterministic",
]
