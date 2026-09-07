"""US-003 — lecture de VRAM et politique du second modele. ADR-003 S4."""

from __future__ import annotations

import subprocess

import pytest

from app.core.config import get_settings
from app.llm import vram


class _Completed:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_vram_none_when_nvidia_smi_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un poste sans GPU NVIDIA fonctionne en mode degrade, il ne plante pas."""
    monkeypatch.setattr(vram.shutil, "which", lambda _: None)
    assert vram.read_vram_used_mb() is None
    assert vram.read_vram_total_mb() is None


def test_vram_read_from_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vram.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(vram.subprocess, "run", lambda *a, **k: _Completed("9544, 10240\n"))
    assert vram.read_vram_used_mb() == 9544
    assert vram.read_vram_total_mb() == 10240


def test_vram_none_when_output_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vram.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(vram.subprocess, "run", lambda *a, **k: _Completed("pilote absent\n"))
    assert vram.read_vram_total_mb() is None


def test_vram_none_when_nvidia_smi_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vram.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(vram.subprocess, "run", lambda *a, **k: _Completed("", 9, "erreur"))
    assert vram.read_vram_total_mb() is None


def test_vram_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)

    monkeypatch.setattr(vram.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(vram.subprocess, "run", boom)
    assert vram.read_vram_total_mb() is None


def test_policy_refuses_when_option_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "code_model_enabled", False)
    assert not vram.policy_allows_code_model(24_576)


def test_policy_refuses_below_12gb(monkeypatch: pytest.MonkeyPatch) -> None:
    """10 Go est le materiel cible : l'option doit y etre refusee."""
    monkeypatch.setattr(get_settings(), "code_model_enabled", True)
    assert not vram.policy_allows_code_model(10_240)
    assert not vram.policy_allows_code_model(12_287)


def test_policy_allows_at_or_above_12gb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "code_model_enabled", True)
    assert vram.policy_allows_code_model(12_288)
    assert vram.policy_allows_code_model(24_576)


def test_policy_refuses_unknown_vram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "code_model_enabled", True)
    assert not vram.policy_allows_code_model(None)
