#!/usr/bin/env python3
"""Verifie la detection d'alteration du journal (ADR-009) sur cette machine.

Scenario : 5000 entrees inserees, chaine verifiee, 2500e entree alteree
directement en SQL — exactement ce que peut faire le proprietaire du fichier
— puis chaine verifiee de nouveau. La chaine doit signaler ALTERE et situer
la premiere incoherence.

Ce script mesure aussi la duree de verification : une detection qui prendrait
plusieurs minutes sur une these ne serait pas utilisee.

Sortie : 0 conforme - 1 non conforme.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from app.db.migrations.runner import run_migrations
    from app.db.session import connect, transaction
    from app.models.audit import AuditEventType, ChainStatus
    from app.services.audit_service import append, verify_chain
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

N_ENTRIES = 5000
TAMPER_INDEX = 2500
NOW = datetime.now(UTC).isoformat()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ECHEC'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


async def run(db: Path) -> None:
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'Verif','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )

        debut = time.perf_counter()
        for i in range(N_ENTRIES):
            async with transaction(conn):
                await append(
                    conn,
                    1,
                    AuditEventType.STATE_TRANSITION,
                    {"index": i, "vers": "SECTION_DRAFTING"},
                    tx=True,
                )
        insertion_s = time.perf_counter() - debut
        check(f"insertion de {N_ENTRIES} entrees", True, f"{insertion_s:.1f} s")

        debut = time.perf_counter()
        avant = await verify_chain(conn, 1)
        verif_s = time.perf_counter() - debut
        check(
            "chaine intacte verifiee VALIDE",
            avant.status is ChainStatus.VALIDE and avant.entries_checked == N_ENTRIES,
            f"{avant.entries_checked} entrees en {verif_s:.2f} s",
        )

    # Alteration hors application, comme le ferait un editeur SQLite.
    con = sqlite3.connect(db)
    try:
        cible = con.execute(
            "SELECT id FROM audit_log ORDER BY id LIMIT 1 OFFSET ?", (TAMPER_INDEX,)
        ).fetchone()[0]
        con.execute(
            "UPDATE audit_log SET payload_json = ? WHERE id = ?",
            ('{"index":-1,"vers":"FALSIFIE"}', cible),
        )
        con.commit()
    finally:
        con.close()

    async with connect(db) as conn:
        debut = time.perf_counter()
        apres = await verify_chain(conn, 1)
        detection_s = time.perf_counter() - debut

    check("alteration detectee", apres.status is ChainStatus.ALTERE, str(apres.status))
    check(
        f"premiere incoherence a l'index {TAMPER_INDEX}",
        apres.first_invalid_index == TAMPER_INDEX,
        f"index {apres.first_invalid_index}",
    )
    check(
        "entree fautive identifiee en base",
        apres.first_invalid_id == cible,
        f"id {apres.first_invalid_id}",
    )
    check("detection sous 5 s", detection_s < 5.0, f"{detection_s:.2f} s")


def main() -> int:
    print("check_audit_chain - ADR-009\n")
    print(f"  Python {sys.version.split()[0]} - {sys.platform}\n")
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run(Path(tmp) / "projet.sqlite"))
    ok = sum(1 for _, o, _ in results if o)
    print(f"\n{'=' * 60}\ncheck_audit_chain : {ok}/{len(results)} conformes")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
