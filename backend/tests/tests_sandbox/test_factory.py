"""US-004 — fabrique : la règle d'isolation, imposée et non déclarée.

Tests purs, sans runtime : la fabrique ne fait que CHOISIR un exécuteur. Son
invariant central — une origine agent n'obtient jamais autre chose que le
niveau 1 — se vérifie sans rien exécuter.
"""

from __future__ import annotations

import logging

import pytest

from app.core.errors import UnsupportedPlatformError
from app.sandbox.base import SandboxLevel, SandboxMode, SandboxOrigin
from app.sandbox.factory import select
from app.sandbox.native_linux import NativeLinuxSandbox
from app.sandbox.native_windows import NativeWindowsSandbox
from app.sandbox.wasm import WasmSandbox


@pytest.mark.parametrize("mode", [SandboxMode.WASM, SandboxMode.NATIVE])
def test_factory_agent_origin_always_wasm(mode: SandboxMode) -> None:
    """Quel que soit le niveau demandé, l'agent reçoit le niveau 1."""
    executor = select(SandboxOrigin.AGENT, mode, platform="win32")
    assert isinstance(executor, WasmSandbox)
    assert executor.level == SandboxLevel.WASM


def test_factory_agent_native_mode_downgraded_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Le niveau natif demandé par un agent n'est pas une erreur : il est
    ramené au niveau 1, et l'abaissement est journalisé."""
    with caplog.at_level(logging.INFO):
        executor = select(SandboxOrigin.AGENT, SandboxMode.NATIVE, platform="linux")
    assert isinstance(executor, WasmSandbox)
    assert any("ramené au niveau 1" in r.message for r in caplog.records)


def test_factory_user_native_selects_platform_executor() -> None:
    assert isinstance(
        select(SandboxOrigin.USER, SandboxMode.NATIVE, platform="win32"), NativeWindowsSandbox
    )
    assert isinstance(
        select(SandboxOrigin.USER, SandboxMode.NATIVE, platform="linux"), NativeLinuxSandbox
    )


def test_factory_unsupported_platform_raises() -> None:
    with pytest.raises(UnsupportedPlatformError):
        select(SandboxOrigin.USER, SandboxMode.NATIVE, platform="sunos5")
    # Le niveau 1 existe partout : une plateforme inconnue ne l'empêche pas.
    executor = select(SandboxOrigin.USER, SandboxMode.WASM, platform="sunos5")
    assert isinstance(executor, WasmSandbox)
