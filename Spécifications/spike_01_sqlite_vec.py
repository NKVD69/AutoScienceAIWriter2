#!/usr/bin/env python3
"""
SPIKE 01 — sqlite-vec : chargement, intégrité référentielle, performance KNN.

Hypothèses testées
  H1.1  L'extension sqlite-vec se charge sur l'interpréteur Python de la machine cible.
  H1.2  Une clé étrangère déclarée sur une table virtuelle vec0 n'est PAS appliquée.
  H1.3  Le schéma corrigé (chunk relationnel + vec_chunk + trigger) applique bien
        l'intégrité et la cascade.
  H1.4  Une recherche KNN sur 50 000 chunks reste sous 200 ms, 400 ms avec filtres.

Décision associée : ADR-002. Un échec de H1.1 bloque tout le projet.
Usage : python spike_01_sqlite_vec.py
Sortie : 0 conforme · 1 non conforme · 2 environnement insuffisant
"""
from __future__ import annotations
import os, random, sqlite3, statistics, sys, tempfile, time

DIM = 768
N_CHUNKS = int(os.environ.get("SPIKE_N_CHUNKS", 50_000))
N_QUERIES = 20
BUDGET_PLAIN_MS = 200.0
BUDGET_FILTERED_MS = 400.0

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ÉCHEC'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    print("SPIKE 01 — sqlite-vec\n")
    print(f"  Python {sys.version.split()[0]} · SQLite {sqlite3.sqlite_version} · {sys.platform}")

    # ---- H1.1 chargement -----------------------------------------------
    print("\nH1.1 — Chargement de l'extension")
    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        print("  [ÉCHEC] Le module sqlite3 de cet interpréteur ne supporte pas les extensions.")
        print("          Remédiation : installer Python depuis python.org, ou pysqlite3-binary.")
        return 2
    try:
        import sqlite_vec
    except ImportError:
        print("  [ÉCHEC] paquet sqlite-vec absent. pip install sqlite-vec")
        return 2

    path = os.path.join(tempfile.mkdtemp(prefix="spike01_"), "p.sqlite")
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception as exc:  # noqa: BLE001
        print(f"  [ÉCHEC] chargement impossible : {exc}")
        return 2
    version = conn.execute("select vec_version()").fetchone()[0]
    check("extension chargée", True, f"vec_version {version}")

    for pragma, expected in (("journal_mode=WAL", "wal"), ("foreign_keys=ON", None)):
        conn.execute(f"PRAGMA {pragma}")
    check("WAL actif", conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal")
    check("foreign_keys actif", conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1)

    # ---- H1.2 FK sur table virtuelle -----------------------------------
    print("\nH1.2 — Clé étrangère déclarée sur une table virtuelle vec0")
    conn.execute("CREATE TABLE source(id INTEGER PRIMARY KEY, title TEXT, year INTEGER, is_preprint INT DEFAULT 0)")
    conn.execute("INSERT INTO source(id,title,year) VALUES (1,'src',2020)")
    fk_ignored = False
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE vbad USING vec0("
            f"embedding float[{DIM}], source_id integer REFERENCES source(id))"
        )
        vec = sqlite_vec.serialize_float32([0.0] * DIM)
        try:
            conn.execute("INSERT INTO vbad(rowid,embedding,source_id) VALUES (1,?,999)", (vec,))
            fk_ignored = True
        except sqlite3.IntegrityError:
            fk_ignored = False
        conn.execute("DROP TABLE vbad")
    except sqlite3.OperationalError as exc:
        check("CREATE VIRTUAL TABLE avec REFERENCES", True, f"rejeté à la création : {exc}")
        fk_ignored = True
    else:
        check(
            "FK sur vec0 ignorée (attendu)",
            fk_ignored,
            "insertion d'un source_id inexistant acceptée" if fk_ignored else "contrainte appliquée — réexaminer ADR-002",
        )

    # ---- H1.3 schéma corrigé -------------------------------------------
    print("\nH1.3 — Schéma corrigé : chunk relationnel + vec_chunk + trigger")
    conn.executescript(
        f"""
        CREATE TABLE chunk(
          id INTEGER PRIMARY KEY,
          source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
          ordinal INTEGER NOT NULL,
          text TEXT NOT NULL,
          UNIQUE(source_id, ordinal));
        CREATE VIRTUAL TABLE vec_chunk USING vec0(embedding float[{DIM}]);
        CREATE TRIGGER chunk_ad AFTER DELETE ON chunk
        BEGIN DELETE FROM vec_chunk WHERE rowid = old.id; END;
        CREATE INDEX idx_chunk_source ON chunk(source_id);
        """
    )
    try:
        conn.execute("INSERT INTO chunk(source_id,ordinal,text) VALUES (999,0,'x')")
        check("FK appliquée sur chunk", False, "source_id inexistant accepté")
    except sqlite3.IntegrityError:
        check("FK appliquée sur chunk", True, "FOREIGN KEY constraint failed")

    cid = conn.execute("INSERT INTO chunk(source_id,ordinal,text) VALUES (1,0,'hello')").lastrowid
    conn.execute("INSERT INTO vec_chunk(rowid,embedding) VALUES (?,?)",
                 (cid, sqlite_vec.serialize_float32([0.0] * DIM)))
    check("invariant rowid", conn.execute("SELECT rowid FROM vec_chunk").fetchone()[0] == cid)
    conn.execute("DELETE FROM source WHERE id=1")
    check("cascade relationnelle", conn.execute("SELECT count(*) FROM chunk").fetchone()[0] == 0)
    check("cascade vectorielle par trigger", conn.execute("SELECT count(*) FROM vec_chunk").fetchone()[0] == 0)

    # ---- H1.4 performance ----------------------------------------------
    print(f"\nH1.4 — Performance KNN sur {N_CHUNKS:,} chunks".replace(",", " "))
    rng = random.Random(20260906)
    n_sources = max(10, N_CHUNKS // 100)
    conn.executemany(
        "INSERT INTO source(id,title,year,is_preprint) VALUES (?,?,?,?)",
        [(i, f"source {i}", rng.randint(1995, 2026), 1 if rng.random() < 0.15 else 0)
         for i in range(1, n_sources + 1)],
    )
    t0 = time.perf_counter()
    conn.execute("BEGIN")
    for i in range(1, N_CHUNKS + 1):
        sid = (i % n_sources) + 1
        conn.execute("INSERT INTO chunk(id,source_id,ordinal,text) VALUES (?,?,?,?)",
                     (i, sid, i, f"chunk {i}"))
        conn.execute("INSERT INTO vec_chunk(rowid,embedding) VALUES (?,?)",
                     (i, sqlite_vec.serialize_float32([rng.random() for _ in range(DIM)])))
    conn.commit()
    print(f"  indexation : {time.perf_counter() - t0:.1f} s")

    def timed(sql: str, params) -> float:
        s = time.perf_counter()
        conn.execute(sql, params).fetchall()
        return (time.perf_counter() - s) * 1000

    q = [sqlite_vec.serialize_float32([rng.random() for _ in range(DIM)]) for _ in range(N_QUERIES)]
    plain = [timed(
        "SELECT c.id, c.text, v.distance FROM vec_chunk v JOIN chunk c ON c.id=v.rowid "
        "WHERE v.embedding MATCH ? AND k=5 ORDER BY v.distance", (e,)) for e in q]
    filt = [timed(
        "SELECT c.id, c.text, v.distance FROM vec_chunk v "
        "JOIN chunk c ON c.id=v.rowid JOIN source s ON s.id=c.source_id "
        "WHERE v.embedding MATCH ? AND k=20 AND s.year>=2020 AND s.is_preprint=0 "
        "ORDER BY v.distance LIMIT 5", (e,)) for e in q]

    p50, p95 = statistics.median(plain), sorted(plain)[int(0.95 * len(plain)) - 1]
    f50, f95 = statistics.median(filt), sorted(filt)[int(0.95 * len(filt)) - 1]
    print(f"  KNN simple   p50 {p50:6.1f} ms · p95 {p95:6.1f} ms")
    print(f"  KNN filtré   p50 {f50:6.1f} ms · p95 {f95:6.1f} ms")
    check(f"KNN simple p95 < {BUDGET_PLAIN_MS:.0f} ms", p95 < BUDGET_PLAIN_MS, f"{p95:.1f} ms")
    check(f"KNN filtré p95 < {BUDGET_FILTERED_MS:.0f} ms", f95 < BUDGET_FILTERED_MS, f"{f95:.1f} ms")

    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"  taille du fichier : {size_mb:.0f} Mo pour {N_CHUNKS:,} chunks".replace(",", " "))

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print(f"SPIKE 01 : {len(results) - len(failed)}/{len(results)} conformes")
    if failed:
        print("Non conformes : " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
