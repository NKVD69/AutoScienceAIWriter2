#!/usr/bin/env python3
"""Verifie les garanties d'ADR-002 sur cette machine.

Controles :
  1. L'extension sqlite-vec se charge sur l'interpreteur courant.
  2. Le schema du projet s'applique sans erreur, table virtuelle comprise.
  3. Aucune clause REFERENCES n'est declaree sur vec_chunk.
  4. 100 vecteurs de dimension configuree s'inserent et l'invariant
     vec_chunk.rowid == chunk.id tient.
  5. Une recherche KNN retourne k resultats portant leur provenance.
  6. La suppression d'une source retire ses chunks (cascade) ET leurs
     vecteurs (trigger).

Sortie : 0 conforme - 1 non conforme - 2 environnement insuffisant.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from app.core.config import get_settings
    from app.core.errors import ExtensionLoadError
    from app.db.migrations.runner import run_migrations
    from app.db.session import connect, transaction, vec_version
    from app.db.vector import insert_chunk_with_embedding, search_similar_chunks
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

N_CHUNKS = 100
K = 5
NOW = datetime.now(UTC).isoformat()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ECHEC'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def vector(seed: int, dim: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(dim)]


async def run(db: Path) -> None:
    dim = get_settings().embedding_dim

    async with connect(db) as conn:
        check("extension chargee", True, f"vec_version {await vec_version(conn)}")

        await run_migrations(conn)
        async with conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name IN"
            " ('chunk','vec_chunk','chunk_after_delete')"
        ) as cur:
            present = (await cur.fetchone())[0]
        check("schema applique", present == 3, f"{present}/3 objets vectoriels")

        async with conn.execute("SELECT sql FROM sqlite_master WHERE name='vec_chunk'") as cur:
            ddl = (await cur.fetchone())[0]
        check("aucune REFERENCES sur vec_chunk", "REFERENCES" not in ddl.upper())

        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'Verif','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            for sid, year, preprint in ((1, 2015, 0), (2, 2022, 0), (3, 2023, 1)):
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, year, doi,"
                    " is_preprint, imported_at) VALUES (?,1,'article',?,?,?,?,?)",
                    (sid, f"Source {sid}", year, f"10.1000/{sid}", preprint, NOW),
                )

        ids: list[int] = []
        for i in range(N_CHUNKS):
            ids.append(
                await insert_chunk_with_embedding(
                    conn,
                    source_id=(i % 3) + 1,
                    ordinal=i // 3,
                    text=f"chunk {i}",
                    embedding=vector(i, dim),
                    page_start=i + 1,
                    page_end=i + 1,
                )
            )
        check("insertion de 100 vecteurs", len(ids) == N_CHUNKS, f"dimension {dim}")

        async with conn.execute(
            "SELECT count(*) FROM chunk c JOIN vec_chunk v ON c.id = v.rowid"
        ) as cur:
            joined = (await cur.fetchone())[0]
        check("invariant rowid", joined == N_CHUNKS, f"{joined}/{N_CHUNKS} jointures")

        hits = await search_similar_chunks(conn, vector(0, dim), k=K)
        provenance = all(h.source_title and h.page_start is not None for h in hits)
        check("KNN avec provenance", len(hits) == K and provenance, f"{len(hits)} resultats")

        filtres = await search_similar_chunks(
            conn, vector(0, dim), k=K, year_min=2020, exclude_preprints=True
        )
        ok_filtre = all(
            h.source_year is not None and h.source_year >= 2020 and not h.is_preprint
            for h in filtres
        )
        check("KNN filtre (annee, preprints)", ok_filtre, f"{len(filtres)} resultats")

        async with conn.execute("SELECT count(*) FROM chunk WHERE source_id=1") as cur:
            avant = (await cur.fetchone())[0]
        async with transaction(conn):
            await conn.execute("DELETE FROM source_document WHERE id=1")
        async with conn.execute("SELECT count(*) FROM chunk WHERE source_id=1") as cur:
            apres_chunks = (await cur.fetchone())[0]
        async with conn.execute("SELECT count(*) FROM vec_chunk") as cur:
            apres_vecteurs = (await cur.fetchone())[0]

        check("cascade relationnelle", apres_chunks == 0, f"{avant} chunks supprimes")
        check(
            "cascade vectorielle par trigger",
            apres_vecteurs == N_CHUNKS - avant,
            f"{apres_vecteurs} vecteurs restants",
        )


def main() -> int:
    print("check_sqlite_vec - ADR-002\n")
    print(f"  Python {sys.version.split()[0]} - SQLite {sqlite3.sqlite_version} - {sys.platform}\n")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp) / "projet.sqlite"))
    except ExtensionLoadError as exc:
        print(f"\n  [NON MESURE] {exc}")
        return 2
    ok = sum(1 for _, o, _ in results if o)
    print(f"\n{'=' * 60}\ncheck_sqlite_vec : {ok}/{len(results)} conformes")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
