"""Bac à sable de niveau 1 — Pyodide/WebAssembly — US-004, ADR-005, ADR-010.

**C'est l'isolation la plus forte, réservée au code le moins fiable.** Tout code
écrit par un agent s'exécute ici, quel que soit le niveau demandé : le runtime
WebAssembly n'a ni pile réseau ni accès disque hors des montages, et cette
garantie tient au runtime lui-même, identique sous Windows et Linux. Un agent ne
peut pas en sortir parce qu'il n'y a rien à contourner.

**Le système de fichiers est reconstruit, pas partagé.** Le répertoire de sortie
est le seul chemin inscriptible, monté vers l'hôte (NODEFS) pour y déposer les
figures. Les montages en lecture seule sont RECOPIÉS dans le système virtuel en
mémoire puis passés en lecture seule : l'hôte n'est jamais exposé en écriture, et
une tentative d'écriture lève une erreur côté invité plutôt que d'atteindre le
disque. Tout le reste de l'hôte est simplement absent du système virtuel.

**La distribution Pyodide est vendorisée hors-ligne (ADR-010).** `indexURL`
pointe sur `runtime/node_modules/pyodide` ; rien n'est téléchargé au lancement.
Le choix du runtime est encapsulé dans `_spawn_runtime()` pour rester
substituable — un binding Python de Pyodide, le jour où il existe, s'y logerait
sans toucher au reste.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import WasmRuntimeUnavailableError
from app.core.logging import get_logger
from app.sandbox.base import (
    ExecutionResult,
    MountSpec,
    ResourceLimits,
    SandboxLevel,
    collect_artifacts,
)
from app.sandbox.limits import check_wasm_imports

logger = get_logger(__name__)

GUEST_OUTPUT = "/output"

# Harnais Node : charge Pyodide hors-ligne, reconstruit le système de fichiers,
# exécute le code invité, et rend un JSON unique sur sa sortie standard. Ses
# propres diagnostics vont sur stderr, séparés du résultat.
_HARNESS_JS = r"""
import path from "node:path";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (data += c));
    process.stdin.on("end", () => resolve(data));
  });
}

function copyReadonly(FS, hostPath, guestPath, maxBytes) {
  const st = fs.statSync(hostPath);
  if (st.isDirectory()) {
    FS.mkdirTree(guestPath);
    for (const name of fs.readdirSync(hostPath)) {
      copyReadonly(FS, path.join(hostPath, name), guestPath + "/" + name, maxBytes);
    }
    FS.chmod(guestPath, 0o555);
  } else {
    if (st.size > maxBytes) throw new Error("montage trop volumineux : " + hostPath);
    FS.writeFile(guestPath, fs.readFileSync(hostPath));
    FS.chmod(guestPath, 0o444);
  }
}

function listArtifacts(root) {
  const out = [];
  function walk(dir, prefix) {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      const rel = prefix ? prefix + "/" + name : name;
      if (fs.statSync(full).isDirectory()) walk(full, rel);
      else out.push(rel);
    }
  }
  if (fs.existsSync(root)) walk(root, "");
  return out;
}

async function main() {
  const job = JSON.parse(await readStdin());
  const outBuf = [], errBuf = [];
  const mod = await import(pathToFileURL(path.join(job.indexURL, "pyodide.mjs")).href);
  const py = await mod.loadPyodide({
    indexURL: job.indexURL,
    stdout: (s) => outBuf.push(s + "\n"),
    stderr: (s) => errBuf.push(s + "\n"),
  });
  const FS = py.FS;
  let exit_code = 0;
  try {
    FS.mkdirTree(job.guestOutput);
    FS.mount(FS.filesystems.NODEFS, { root: job.hostOutput }, job.guestOutput);
    for (const m of job.mounts) {
      if (m.writable) {
        FS.mkdirTree(m.guest);
        FS.mount(FS.filesystems.NODEFS, { root: m.host }, m.guest);
      } else {
        copyReadonly(FS, m.host, m.guest, job.maxFileBytes);
      }
    }
    await py.runPythonAsync(
      "import os\nos.environ['MPLBACKEND'] = 'Agg'\nos.chdir('" + job.guestOutput + "')"
    );
    await py.runPythonAsync(job.code);
  } catch (e) {
    exit_code = 1;
    errBuf.push(String((e && e.message) || e));
  }
  const artifacts = listArtifacts(job.hostOutput);
  const result = { exit_code, stdout: outBuf.join(""), stderr: errBuf.join(""), artifacts };
  process.stdout.write(JSON.stringify(result));
}

main().catch((e) => {
  process.stderr.write("HARNESS_FATAL: " + String((e && e.stack) || e));
  process.exit(3);
});
"""

_harness_path: Path | None = None


def _harness_file() -> Path:
    """Écrit le harnais dans un fichier temporaire, une fois, et le réutilise.

    Node a besoin d'un fichier module ; l'embarquer en constante évite d'ajouter
    un `.mjs` au dépôt, et le fichier temporaire est régénéré à chaque process.
    """
    global _harness_path
    if _harness_path is not None and _harness_path.exists():
        return _harness_path
    fd, nom = tempfile.mkstemp(prefix="saw-wasm-", suffix=".mjs")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_HARNESS_JS)
    _harness_path = Path(nom)
    return _harness_path


def default_index_url() -> Path:
    """Distribution Pyodide vendorisée, sous le paquet sandbox."""
    return Path(__file__).parent / "runtime" / "node_modules" / "pyodide"


def _index_url() -> Path:
    reglage = get_settings().pyodide_index_url
    return Path(reglage) if reglage is not None else default_index_url()


async def _terminate_tree(proc: asyncio.subprocess.Process) -> None:
    """Tue le processus Node et toute sa descendance.

    Un `terminate()` simple laisserait le sous-processus Node — et le runtime
    Wasm qu'il porte — vivant après un dépassement mural.
    """
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            tueur = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await tueur.wait()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


class WasmSandbox:
    """Exécuteur de niveau 1. Réseau et disque isolés par le runtime."""

    level = SandboxLevel.WASM

    def __init__(self, node_executable: str | None = None) -> None:
        self._node = node_executable or get_settings().node_executable

    async def _spawn_runtime(self) -> asyncio.subprocess.Process:
        """Démarre le runtime Pyodide dans un sous-processus Node.

        Point de substitution unique : ce qui change entre « Node + Pyodide » et
        un futur binding Python tient ici, le reste de `run` n'en dépend pas.
        """
        index = _index_url()
        if not (index / "pyodide.mjs").exists():
            raise WasmRuntimeUnavailableError.actionable(f"distribution Pyodide absente de {index}")
        try:
            return await asyncio.create_subprocess_exec(
                self._node,
                str(_harness_file()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Groupe de processus dédié : permet de tuer tout l'arbre au
                # dépassement mural sous POSIX. Sous Windows, taskkill /T s'en charge.
                start_new_session=sys.platform != "win32",
            )
        except FileNotFoundError as exc:
            raise WasmRuntimeUnavailableError.actionable(
                f"exécutable Node introuvable : « {self._node} »"
            ) from exc

    def _build_job(
        self, code: str, mounts: list[MountSpec], limits: ResourceLimits, output_dir: Path
    ) -> str:
        return json.dumps(
            {
                "indexURL": str(_index_url()),
                "code": code,
                "guestOutput": GUEST_OUTPUT,
                "hostOutput": str(output_dir),
                "mounts": [
                    {
                        "host": str(m.host_path),
                        "guest": str(m.guest_path),
                        "writable": m.writable,
                    }
                    for m in mounts
                ],
                "maxFileBytes": limits.max_file_mb * 1024 * 1024,
            }
        )

    async def run(
        self,
        code: str,
        mounts: list[MountSpec],
        limits: ResourceLimits,
        output_dir: Path,
    ) -> ExecutionResult:
        """Exécute `code` au niveau 1. Le timeout mural est tenu par CE superviseur.

        Un import hors liste blanche est refusé AVANT tout démarrage : inutile de
        payer le coût d'un runtime pour un code qui n'a pas sa place au niveau 1.
        """
        check_wasm_imports(code)
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

        debut = time.monotonic()
        proc = await self._spawn_runtime()
        job_bytes = self._build_job(code, mounts, limits, output_dir).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=job_bytes), timeout=limits.wall_seconds
            )
        except TimeoutError:
            await _terminate_tree(proc)
            duree = int((time.monotonic() - debut) * 1000)
            return ExecutionResult(
                exit_code=124,
                stdout="",
                stderr=(
                    f"Délai mural de {limits.wall_seconds}s dépassé : exécution "
                    "interrompue par le superviseur."
                ),
                duration_ms=duree,
                level=SandboxLevel.WASM,
                timed_out=True,
                limit_exceeded="wall",
                artifacts=[],
                network_isolation_guaranteed=True,
            )
        duree = int((time.monotonic() - debut) * 1000)

        diag = stderr.decode("utf-8", "replace")
        try:
            charge = json.loads(stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise WasmRuntimeUnavailableError.actionable(
                f"sortie du runtime illisible (code {proc.returncode}) : {diag[-500:]}"
            ) from exc

        artefacts = await asyncio.to_thread(collect_artifacts, output_dir)
        return ExecutionResult(
            exit_code=int(charge["exit_code"]),
            stdout=charge["stdout"],
            stderr=charge["stderr"],
            duration_ms=duree,
            level=SandboxLevel.WASM,
            timed_out=False,
            limit_exceeded=None,
            artifacts=artefacts,
            # Garanti par le runtime, sur tout OS : c'est la raison d'être du niveau 1.
            network_isolation_guaranteed=True,
        )
