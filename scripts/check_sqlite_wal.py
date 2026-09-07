#!/usr/bin/env python3
"""Vérifie les garanties d'ADR-001 sur cette machine.

Contrôles :
  1. `journal_mode=WAL` est réellement actif sur un fichier réel.
  2. `foreign_keys=ON` est appliqué et une violation est bien refusée.
  3. `busy_timeout` absorbe une contention d'écriture concurrente sans
     `database is locked` — la preuve qu'aucune file applicative n'est requise.
  4. Un `wal_checkpoint(TRUNCATE)` suivi d'une copie produit un fichier autonome.

Sortie : 0 conforme · 1 non conforme · 2 environnement insuffisant.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    import aiosqlite
except ImportError:
    print("aiosqlite absent : pip install -e .[dev]")
    sys.exit(2)

BUSY_TIMEOUT_MS = 5000
N_WRITERS = 24
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ECHEC'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


async def _connect(path: Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(path))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def run(db: Path) -> None:
    conn = await _connect(db)
    async with conn.execute("PRAGMA journal_mode") as cur:
        mode = (await cur.fetchone())[0].lower()
    check("journal_mode=WAL actif", mode == "wal", mode)
    async with conn.execute("PRAGMA foreign_keys") as cur:
        fk = (await cur.fetchone())[0]
    check("foreign_keys actif", bool(fk))

    await conn.executescript(
        "CREATE TABLE parent(id INTEGER PRIMARY KEY, label TEXT NOT NULL);"
        "CREATE TABLE child(id INTEGER PRIMARY KEY,"
        " parent_id INTEGER NOT NULL REFERENCES parent(id) ON DELETE CASCADE,"
        " value TEXT NOT NULL);"
    )
    await conn.execute("INSERT INTO parent(id,label) VALUES (1,'racine')")
    await conn.commit()

    try:
        await conn.execute("INSERT INTO child(parent_id,value) VALUES (999,'orphelin')")
        await conn.commit()
        check("violation de cle etrangere refusee", False, "insertion acceptee")
    except aiosqlite.IntegrityError as exc:
        check("violation de cle etrangere refusee", "FOREIGN KEY" in str(exc), str(exc))
    await conn.close()

    async def writer(n: int) -> str | None:
        try:
            c = await _connect(db)
            await c.execute("BEGIN")
            await c.execute("INSERT INTO child(parent_id,value) VALUES (1,?)", (f"v{n}",))
            await c.commit()
            await c.close()
            return None
        except Exception as exc:  # on veut le message brut du moteur
            return str(exc)

    errors = [e for e in await asyncio.gather(*(writer(i) for i in range(N_WRITERS))) if e]
    check(
        f"{N_WRITERS} ecritures concurrentes sans verrou",
        not errors,
        errors[0] if errors else f"{N_WRITERS} lignes ecrites",
    )

    conn = await _connect(db)
    await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await conn.commit()
    await conn.close()
    copy = db.with_name("copie.sqlite")
    shutil.copy2(db, copy)
    con = sqlite3.connect(copy)
    try:
        n = con.execute("SELECT count(*) FROM child").fetchone()[0]
    finally:
        con.close()
    check("copie apres checkpoint autonome", n == N_WRITERS, f"{n} lignes relues sans -wal")


def main() -> int:
    print("check_sqlite_wal - ADR-001\n")
    print(f"  Python {sys.version.split()[0]} - SQLite {sqlite3.sqlite_version} - {sys.platform}\n")
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run(Path(tmp) / "projet.sqlite"))
    ok = sum(1 for _, o, _ in results if o)
    print(f"\n{'=' * 60}\ncheck_sqlite_wal : {ok}/{len(results)} conformes")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
