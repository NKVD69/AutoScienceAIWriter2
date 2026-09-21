"""Bac à sable de niveau 2 sous Windows — Job Objects — US-004, ADR-005.

**Ce niveau borne les ressources, il n'isole PAS le réseau.** Les Job Objects
Win32 plafonnent la mémoire, le temps CPU et le nombre de processus, et tuent
toute la descendance à la fermeture du Job. Mais ils n'ont aucune prise sur les
sockets : `network_isolation_guaranteed` vaut donc `False`, sans exception
(ADR-005, D-03). C'est la raison pour laquelle ce niveau exige un consentement
explicite et n'accueille jamais du code d'agent.

**Le processus est créé SUSPENDU, assigné au Job, puis repris.** L'ordre est la
garantie : si le code invité pouvait s'exécuter avant l'assignation, il tournerait
un instant hors de toute limite. L'assignation précède toujours la reprise.

Les modules pywin32 sont importés dans la méthode, pas au module : `factory.py`
doit pouvoir importer cette classe sous Linux — pour l'y écarter proprement —
sans que pywin32, absent, fasse échouer l'import.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from app.core.logging import get_logger
from app.sandbox.base import ExecutionResult, MountSpec, ResourceLimits, SandboxLevel

logger = get_logger(__name__)

_HUNDRED_NS_PER_SECOND = 10_000_000


class NativeWindowsSandbox:
    """Exécuteur natif Windows. Ressources bornées, réseau NON isolé."""

    level = SandboxLevel.NATIVE

    async def run(
        self,
        code: str,
        mounts: list[MountSpec],
        limits: ResourceLimits,
        output_dir: Path,
    ) -> ExecutionResult:
        import asyncio

        # L'appel Win32 est bloquant (WaitForSingleObject) : on le tient hors de
        # l'event loop pour ne pas geler le serveur pendant une exécution longue.
        return await asyncio.to_thread(self._run_blocking, code, limits, output_dir)

    def _run_blocking(self, code: str, limits: ResourceLimits, output_dir: Path) -> ExecutionResult:
        import sys

        import win32con
        import win32event
        import win32file
        import win32job
        import win32process
        import win32security

        output_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="saw-native-"))
        script = work / "job.py"
        script.write_text(code, encoding="utf-8")
        out_path = work / "stdout.txt"
        err_path = work / "stderr.txt"

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        basic = info["BasicLimitInformation"]
        basic["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | win32job.JOB_OBJECT_LIMIT_PROCESS_TIME
            | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
            | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win32job.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        )
        basic["ActiveProcessLimit"] = limits.max_processes
        basic["PerProcessUserTimeLimit"] = limits.cpu_seconds * _HUNDRED_NS_PER_SECOND
        info["BasicLimitInformation"] = basic
        info["ProcessMemoryLimit"] = limits.memory_mb * 1024 * 1024
        info["JobMemoryLimit"] = limits.memory_mb * 1024 * 1024
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)

        sa = win32security.SECURITY_ATTRIBUTES()
        sa.bInheritHandle = True
        hout = win32file.CreateFile(
            str(out_path),
            win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            sa,
            win32con.CREATE_ALWAYS,
            0,
            None,
        )
        herr = win32file.CreateFile(
            str(err_path),
            win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            sa,
            win32con.CREATE_ALWAYS,
            0,
            None,
        )

        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdOutput = hout
        startup.hStdError = herr
        startup.hStdInput = None

        # -I : mode isolé (ignore l'environnement et le répertoire courant pour
        # les imports) ; -B : pas de .pyc ; cwd = répertoire de sortie.
        cmd = f'"{sys.executable}" -I -B "{script}"'
        flags = win32con.CREATE_SUSPENDED | win32con.CREATE_NEW_PROCESS_GROUP

        debut = time.monotonic()
        hprocess, hthread, _pid, _tid = win32process.CreateProcess(
            None, cmd, None, None, True, flags, None, str(output_dir), startup
        )
        # ORDRE CRITIQUE : assignation AVANT reprise. Le code invité ne s'exécute
        # jamais hors des limites du Job.
        win32job.AssignProcessToJobObject(job, hprocess)
        win32process.ResumeThread(hthread)
        hout.Close()
        herr.Close()

        attente = win32event.WaitForSingleObject(hprocess, int(limits.wall_seconds * 1000))
        timed_out = attente == win32event.WAIT_TIMEOUT
        limit_exceeded: str | None = None
        if timed_out:
            # Tuer d'abord, pour que l'enfant relâche les fichiers de sortie.
            win32job.TerminateJobObject(job, 1)
            exit_code = 124
            limit_exceeded = "wall"
        else:
            exit_code = win32process.GetExitCodeProcess(hprocess)

        duree = int((time.monotonic() - debut) * 1000)
        stdout = _read(out_path)
        stderr = _read(err_path)
        # Diagnostic AVANT fermeture du Job : le comptage disparaît avec lui.
        if not timed_out and exit_code != 0:
            limit_exceeded = self._diagnose(win32job, job, limits, exit_code, stderr)

        # Fermer le Job tue tout descendant survivant (KILL_ON_JOB_CLOSE).
        for handle in (hprocess, hthread, job):
            try:
                handle.Close()
            except Exception:
                pass

        artefacts = sorted(p for p in output_dir.rglob("*") if p.is_file())
        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duree,
            level=SandboxLevel.NATIVE,
            timed_out=timed_out,
            limit_exceeded=limit_exceeded,
            artifacts=artefacts,
            # Sans exception : les Job Objects n'isolent pas le réseau (ADR-005).
            network_isolation_guaranteed=False,
        )

    @staticmethod
    def _diagnose(win32job, job, limits: ResourceLimits, exit_code: int, stderr: str) -> str | None:
        """Attribue au mieux la cause d'un arrêt anormal : mémoire, CPU ou rien.

        Deux signaux honnêtes, jamais présentés comme certains — Windows ne rend
        pas de motif explicite :
        - une `MemoryError`, ou un pic mémoire au plafond du Job, désigne la
          mémoire : quand `JobMemoryLimit` est atteint, l'allocation échoue et
          Python lève `MemoryError` avant même que le pic n'atteigne le plafond ;
        - un temps utilisateur au plafond désigne le CPU.
        """
        if "MemoryError" in stderr:
            return "memory"
        try:
            ext = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation
            )
            pic = int(ext.get("PeakProcessMemoryUsed", 0))
            if pic >= int(0.9 * limits.memory_mb * 1024 * 1024):
                return "memory"
            acct = win32job.QueryInformationJobObject(
                job, win32job.JobObjectBasicAccountingInformation
            )
            user_100ns = int(acct.get("TotalUserTime", 0))
            if user_100ns >= int(0.9 * limits.cpu_seconds * _HUNDRED_NS_PER_SECOND):
                return "cpu"
        except Exception:
            return None
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
