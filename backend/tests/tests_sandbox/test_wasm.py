"""US-004 — bac à sable de niveau 1 (Pyodide/WebAssembly).

**Les tests d'isolation ne sont jamais conditionnés à la PLATEFORME.** Leur
intérêt est d'être identiques partout : l'isolation du niveau 1 vient du
runtime, pas de l'OS. Ils sont en revanche marqués `integration` — ils
démarrent un vrai Node + Pyodide, un runtime externe au même titre qu'Ollama ou
Quarto — et sautent proprement si cette distribution vendorisée est absente,
jamais parce qu'on est sous tel ou tel système.

Le refus d'un paquet hors liste blanche, lui, précède tout démarrage de runtime :
c'est un test unitaire rapide, sans dépendance externe.
"""

from __future__ import annotations

import shutil

import pytest

from app.core.errors import PackageUnavailableInWasmError
from app.sandbox.base import MountSpec, ResourceLimits
from app.sandbox.wasm import WasmSandbox, default_index_url

_RUNTIME_OK = shutil.which("node") is not None and (default_index_url() / "pyodide.mjs").exists()
needs_runtime = pytest.mark.skipif(
    not _RUNTIME_OK, reason="runtime Pyodide/Node absent (distribution vendorisée non installée)"
)


@pytest.fixture
def sandbox() -> WasmSandbox:
    return WasmSandbox()


# --- Liste blanche : refus AVANT tout runtime (unitaire, rapide) ----------


async def test_wasm_package_not_whitelisted_suggests_level2(sandbox: WasmSandbox, tmp_path) -> None:
    """Un import hors liste ne paie pas le coût d'un runtime : il est refusé
    d'emblée, avec le paquet nommé et le niveau 2 proposé."""
    with pytest.raises(PackageUnavailableInWasmError) as exc:
        await sandbox.run("import requests\n", [], ResourceLimits(), tmp_path / "out")
    assert exc.value.details["package"] == "requests"
    assert exc.value.details["suggested_level"] == 2
    assert "niveau 2" in exc.value.message


# --- Isolation réelle : runtime requis, jamais skip par plateforme --------


@pytest.mark.integration
@needs_runtime
async def test_wasm_network_blocked(sandbox: WasmSandbox, tmp_path) -> None:
    """Identique sous Windows et Linux : pas de pile réseau dans le runtime."""
    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=3)\n"
        "    print('NET_OK')\n"
        "except Exception as e:\n"
        "    print('NET_BLOCKED', type(e).__name__)\n"
    )
    r = await sandbox.run(code, [], ResourceLimits(wall_seconds=30), tmp_path / "out")
    assert "NET_BLOCKED" in r.stdout
    assert "NET_OK" not in r.stdout
    assert r.network_isolation_guaranteed is True


@pytest.mark.integration
@needs_runtime
async def test_wasm_filesystem_isolated_outside_mounts(sandbox: WasmSandbox, tmp_path) -> None:
    """Un chemin hôte non monté est simplement absent du système virtuel."""
    secret = tmp_path / "secret.txt"
    secret.write_text("hors montage", encoding="utf-8")
    code = (
        f"try:\n"
        f"    open({str(secret)!r}).read()\n"
        f"    print('READ_OK')\n"
        f"except Exception as e:\n"
        f"    print('READ_BLOCKED', type(e).__name__)\n"
    )
    r = await sandbox.run(code, [], ResourceLimits(), tmp_path / "out")
    assert "READ_BLOCKED" in r.stdout
    assert "READ_OK" not in r.stdout


@pytest.mark.integration
@needs_runtime
async def test_wasm_readonly_mount_rejects_write(sandbox: WasmSandbox, tmp_path) -> None:
    """Un montage en lecture seule se lit, ne s'écrit pas, et l'hôte est intact."""
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "data.txt").write_text("donnees montees", encoding="utf-8")
    code = (
        "print(open('/mnt/ro/data.txt').read())\n"
        "try:\n"
        "    open('/mnt/ro/nouveau.txt', 'w').write('x')\n"
        "    print('WRITE_OK')\n"
        "except Exception as e:\n"
        "    print('WRITE_BLOCKED', type(e).__name__)\n"
    )
    r = await sandbox.run(
        code, [MountSpec(host_path=ro, guest_path="/mnt/ro")], ResourceLimits(), tmp_path / "out"
    )
    assert "donnees montees" in r.stdout
    assert "WRITE_BLOCKED" in r.stdout
    assert "WRITE_OK" not in r.stdout
    assert not (ro / "nouveau.txt").exists(), "l'hôte ne doit jamais être écrit"


@pytest.mark.integration
@needs_runtime
async def test_wasm_output_dir_writable(sandbox: WasmSandbox, tmp_path) -> None:
    out = tmp_path / "out"
    r = await sandbox.run("open('result.txt', 'w').write('ok')\n", [], ResourceLimits(), out)
    assert r.exit_code == 0
    assert (out / "result.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.integration
@needs_runtime
async def test_wasm_artifacts_collected(sandbox: WasmSandbox, tmp_path) -> None:
    out = tmp_path / "out"
    r = await sandbox.run(
        "open('a.txt','w').write('1')\nopen('b.txt','w').write('2')\n", [], ResourceLimits(), out
    )
    assert sorted(p.name for p in r.artifacts) == ["a.txt", "b.txt"]


@pytest.mark.integration
@needs_runtime
async def test_wasm_timeout_enforced(sandbox: WasmSandbox, tmp_path) -> None:
    """Le délai mural est tenu par le superviseur, pas par le code invité."""
    r = await sandbox.run(
        "while True:\n    pass\n", [], ResourceLimits(wall_seconds=3), tmp_path / "out"
    )
    assert r.timed_out is True
    assert r.limit_exceeded == "wall"
    assert r.exit_code != 0
