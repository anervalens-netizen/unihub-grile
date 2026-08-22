"""Artifact publication must never expose partial replacement bytes."""

from __future__ import annotations

import os

import pytest

from ugrile.worker.jobs import _write_bytes


def test_atomic_write_publishes_complete_payload(tmp_path):
    target = tmp_path / "artifact.xlsx"
    payload = b"PK-complete-payload"

    _write_bytes(str(target), payload)

    assert target.read_bytes() == payload
    assert not list(tmp_path.glob(".ugrile-artifact-*"))


def test_atomic_write_keeps_previous_good_target_when_replace_fails(monkeypatch, tmp_path):
    target = tmp_path / "artifact.xlsx"
    _write_bytes(str(target), b"old-good-bytes")

    def fail_replace(_source, _target):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        _write_bytes(str(target), b"new-bytes-that-must-not-leak")

    assert target.read_bytes() == b"old-good-bytes"
    assert not list(tmp_path.glob(".ugrile-artifact-*"))
