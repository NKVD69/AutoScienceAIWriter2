#!/usr/bin/env python3
"""Verifie les garanties du bac a sable sous Linux (US-004, ADR-005).

Execute en sequence, aux deux niveaux, cinq scripts sondes : inoffensif, acces
reseau, acces disque hors montage, depassement memoire, boucle infinie. Affiche
un tableau niveau / sonde / attendu / obtenu.

Difference avec Windows : au niveau 2, l'isolation reseau est CONDITIONNELLE.
`unshare -n` la fournit s'il reussit ; sans privileges, elle manque. Le script
ne suppose donc pas le resultat : il lit la garantie DECLAREE
(network_isolation_guaranteed) et verifie que la sonde s'y CONFORME — reseau
bloque si l'on annonce l'isolation, joignable sinon. C'est l'honnetete du champ
qui est controlee, pas une isolation supposee acquise.

La sonde reseau vise un echo TCP local, sur 127.0.0.1, demarre par ce script :
rien ne quitte la machine.

Sortie : 0 conforme - 1 non conforme - 2 runtime absent.
"""

from __future__ import annotations

import asyncio
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from app.sandbox.base import ResourceLimits, SandboxMode, SandboxOrigin
    from app.sandbox.factory import select
    from app.sandbox.wasm import default_index_url
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

HARMLESS = "print('inoffensif ok')"
MEMORY = "b = bytearray(400 * 1024 * 1024)\nprint('MEMORY_OK')"
LOOP = "while True:\n    pass\n"


class _Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            self.request.sendall(self.request.recv(16) or b"pong")
        except OSError:
            pass


def start_echo_server() -> tuple[int, socketserver.TCPServer]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], server


def network_probe(port: int) -> str:
    return (
        "import socket\n"
        "try:\n"
        f"    s = socket.create_connection(('127.0.0.1', {port}), timeout=3)\n"
        "    s.sendall(b'ping')\n"
        "    data = s.recv(16)\n"
        "    print('NET_CONNECTED' if data else 'NET_NODATA')\n"
        "except Exception as e:\n"
        "    print('NET_BLOCKED', type(e).__name__)\n"
    )


def disk_probe(host_path: Path) -> str:
    return (
        f"try:\n"
        f"    open({str(host_path)!r}).read()\n"
        f"    print('DISK_REACHABLE')\n"
        f"except Exception as e:\n"
        f"    print('DISK_BLOCKED', type(e).__name__)\n"
    )


results: list[tuple[str, str, str, str, bool]] = []


def record(level: str, probe: str, expected: str, obtained: str, ok: bool) -> None:
    results.append((level, probe, expected, obtained, ok))


async def run_level_1(secret: Path, port: int, tmp: Path) -> None:
    sb = select(SandboxOrigin.AGENT, SandboxMode.WASM)
    lim = ResourceLimits(wall_seconds=8)

    r = await sb.run(HARMLESS, [], lim, tmp / "l1a")
    record("1 Wasm", "inoffensif", "sortie 0", f"exit={r.exit_code}", r.exit_code == 0)

    r = await sb.run(network_probe(port), [], lim, tmp / "l1b")
    record("1 Wasm", "reseau", "isole", r.stdout.strip(), "NET_CONNECTED" not in r.stdout)

    r = await sb.run(disk_probe(secret), [], lim, tmp / "l1c")
    record("1 Wasm", "disque hors montage", "isole", r.stdout.strip(), "DISK_BLOCKED" in r.stdout)

    r = await sb.run(LOOP, [], ResourceLimits(wall_seconds=3), tmp / "l1e")
    record("1 Wasm", "boucle infinie", "delai mural", f"timeout={r.timed_out}", r.timed_out)


async def run_level_2(secret: Path, port: int, tmp: Path) -> None:
    sb = select(SandboxOrigin.USER, SandboxMode.NATIVE, platform="linux")

    r = await sb.run(HARMLESS, [], ResourceLimits(wall_seconds=15), tmp / "l2a")
    record("2 natif", "inoffensif", "sortie 0", f"exit={r.exit_code}", r.exit_code == 0)

    # L'isolation reseau native est conditionnelle a unshare -n : on lit la
    # garantie declaree, puis on verifie que la sonde s'y conforme.
    r = await sb.run(network_probe(port), [], ResourceLimits(wall_seconds=15), tmp / "l2b")
    isole = r.network_isolation_guaranteed
    connecte = "NET_CONNECTED" in r.stdout
    coherent = (isole and not connecte) or (not isole and connecte)
    record(
        "2 natif",
        "reseau (coherence)",
        "isole -> bloque",
        f"declare={isole} sonde={'joint' if connecte else 'bloque'}",
        coherent,
    )

    r = await sb.run(disk_probe(secret), [], ResourceLimits(wall_seconds=15), tmp / "l2c")
    record(
        "2 natif",
        "disque (declare acces)",
        "accessible",
        r.stdout.strip(),
        "DISK_REACHABLE" in r.stdout,
    )

    r = await sb.run(MEMORY, [], ResourceLimits(memory_mb=128, wall_seconds=20), tmp / "l2d")
    tue = "MEMORY_OK" not in r.stdout and r.exit_code != 0
    record("2 natif", "depassement memoire", "processus tue", f"limit={r.limit_exceeded}", tue)

    r = await sb.run(LOOP, [], ResourceLimits(cpu_seconds=30, wall_seconds=3), tmp / "l2e")
    record("2 natif", "boucle infinie", "delai mural", f"timeout={r.timed_out}", r.timed_out)


async def main() -> int:
    if not sys.platform.startswith("linux"):
        print("Ce script vise Linux. Sous Windows : check_sandbox_windows.py.")
        return 2
    if shutil.which("node") is None or not (default_index_url() / "pyodide.mjs").exists():
        print("Runtime Pyodide/Node absent : distribution vendorisee non installee.")
        print("  cd backend/app/sandbox/runtime && npm install")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="saw-checksb-"))
    secret = tmp / "hors_montage.txt"
    secret.write_text("donnee hote, jamais montee", encoding="utf-8")
    port, server = start_echo_server()
    try:
        await run_level_1(secret, port, tmp)
        await run_level_2(secret, port, tmp)
    finally:
        server.shutdown()

    largeur = max(len(p) for _, p, _, _, _ in results)
    print(f"\n{'Niveau':8} {'Sonde':{largeur}} {'Attendu':22} {'Obtenu':30} Etat")
    print("-" * (8 + largeur + 22 + 30 + 8))
    for level, probe, expected, obtained, ok in results:
        etat = "OK" if ok else "ECHEC"
        print(f"{level:8} {probe:{largeur}} {expected:22} {obtained:30} {etat}")

    conforme = all(ok for *_, ok in results)
    print(
        "\ncheck_sandbox_linux : "
        + ("garanties DECLAREES tenues" if conforme else "au moins une garantie non tenue")
    )
    return 0 if conforme else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
