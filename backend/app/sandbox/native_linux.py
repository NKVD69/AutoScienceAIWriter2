"""Bac à sable de niveau 2 sous Linux — setrlimit + unshare — US-004, ADR-005.

**Ce niveau borne les ressources par `setrlimit`, et TENTE d'isoler le réseau
par `unshare -n`.** L'isolation réseau n'est donc pas acquise : `unshare -n`
demande des privilèges qui peuvent manquer. On la tente, on vérifie qu'elle a
pris, et `network_isolation_guaranteed` dit la vérité — `True` seulement si
`unshare -n` a réussi. Jamais d'affirmation d'isolation qu'on n'a pas obtenue.

Les bornes (`RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NPROC`, `RLIMIT_FSIZE`) sont
posées dans un `preexec_fn` : elles s'appliquent au processus enfant, avant
l'exec du code, et survivent à l'exec. Le `setsid` place l'enfant dans son
propre groupe, tué en bloc au dépassement mural.

`resource` et les primitives POSIX sont importés dans les fonctions : `factory.py`
importe cette classe sous Windows — pour l'y écarter — sans que `resource`,
absent, casse l'import.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import tempfile
import time
from pathlib import Path

from app.core.logging import get_logger
from app.sandbox.base import (
    ExecutionResult,
    MountSpec,
    ResourceLimits,
    SandboxLevel,
    collect_artifacts,
)

logger = get_logger(__name__)

_unshare_ok: bool | None = None


def _network_isolation_available() -> bool:
    """Vérifie une fois que `unshare -n` fonctionne réellement sur ce poste.

    Une sonde honnête : `unshare -n true` réussit-il ? Sans privilèges (ni
    namespaces utilisateur), il échoue, et l'on ne prétendra pas isoler le réseau.
    """
    global _unshare_ok
    if _unshare_ok is not None:
        return _unshare_ok
    import shutil
    import subprocess

    _unshare_ok = False
    if shutil.which("unshare"):
        try:
            probe = subprocess.run(
                ["unshare", "-n", "true"], capture_output=True, timeout=5, check=False
            )
            _unshare_ok = probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _unshare_ok = False
    if not _unshare_ok:
        logger.info("unshare -n indisponible : niveau 2 sans isolation réseau garantie.")
    return _unshare_ok


def _apply_limits(limits: ResourceLimits) -> None:
    """preexec : nouveau groupe de processus puis bornes rlimit."""
    import os
    import resource

    os.setsid()
    octets = limits.memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (octets, octets))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    # RLIMIT_NPROC compte les processus de l'utilisateur réel : sous unshare -n
    # (namespace propre) le compte repart, hors namespace il est partagé. On le
    # pose comme demandé (US-004) ; sa portée exacte est notée en réserve.
    resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
    taille = limits.max_file_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (taille, taille))


def _diagnose(returncode: int, stderr: str) -> str | None:
    """Cause d'un arrêt anormal, d'après le signal ou la trace.

    `RLIMIT_CPU` tue par SIGXCPU ; `RLIMIT_AS` fait échouer l'allocation, d'où une
    `MemoryError` côté Python. Un SIGKILL nu reste ambigu (OOM killer, autre) et
    n'est pas surinterprété.
    """
    if returncode == -signal.SIGXCPU:
        return "cpu"
    if "MemoryError" in stderr:
        return "memory"
    return None


class NativeLinuxSandbox:
    """Exécuteur natif Linux. Ressources bornées, réseau isolé si possible."""

    level = SandboxLevel.NATIVE

    async def run(
        self,
        code: str,
        mounts: list[MountSpec],
        limits: ResourceLimits,
        output_dir: Path,
    ) -> ExecutionResult:
        import os

        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="saw-native-"))
        script = work / "job.py"
        script.write_text(code, encoding="utf-8")

        isolated = _network_isolation_available()
        base = [sys.executable, "-I", "-B", str(script)]
        cmd = ["unshare", "-n", *base] if isolated else base

        debut = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(output_dir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: _apply_limits(limits),
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=limits.wall_seconds)
        except TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            await proc.wait()
            out, err = b"", b""
            timed_out = True

        duree = int((time.monotonic() - debut) * 1000)
        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        if timed_out:
            exit_code = 124
            limit_exceeded: str | None = "wall"
        else:
            exit_code = proc.returncode if proc.returncode is not None else 1
            limit_exceeded = _diagnose(exit_code, stderr)

        artefacts = await asyncio.to_thread(collect_artifacts, output_dir)
        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duree,
            level=SandboxLevel.NATIVE,
            timed_out=timed_out,
            limit_exceeded=limit_exceeded,
            artifacts=artefacts,
            network_isolation_guaranteed=isolated,
        )
