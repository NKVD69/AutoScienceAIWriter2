"""US-701 — serialisation canonique, chainage, verification. ADR-009."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.models.audit import AuditEventType, ChainStatus
from app.services import audit_service
from app.services.audit_service import (
    AuditWriteError,
    append,
    canonical,
    compute_hash,
    list_entries,
    verify_chain,
)


async def make_db(db_path: Path) -> None:
    async with connect(db_path) as conn:
        await run_migrations(conn)


async def append_n(db_path: Path, n: int, project_id: int = 1) -> None:
    async with connect(db_path) as conn:
        for i in range(n):
            async with transaction(conn):
                await append(
                    conn,
                    project_id,
                    AuditEventType.STATE_TRANSITION,
                    {"index": i, "to": "PLAN_DRAFTING"},
                    tx=True,
                )


# --- Serialisation canonique ---------------------------------------------


def test_canonical_serialization_stable_across_key_order() -> None:
    a = {"beta": 2, "alpha": 1, "gamma": {"y": 2, "x": 1}}
    b = {"gamma": {"x": 1, "y": 2}, "alpha": 1, "beta": 2}
    assert canonical(a) == canonical(b)


def test_canonical_has_no_whitespace_and_keeps_utf8() -> None:
    out = canonical({"titre": "modélisation", "n": 1})
    assert b", " not in out and b'": ' not in out
    assert "modélisation".encode() in out


def test_canonical_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError, match="NaN"):
        canonical({"score": float("nan")})
    with pytest.raises(ValueError, match="Infinity"):
        canonical({"score": float("inf")})
    with pytest.raises(ValueError):
        canonical({"liste": [1.0, float("-inf")]})
    with pytest.raises(ValueError):
        canonical({"imbrique": {"a": float("nan")}})


def test_hash_depends_on_previous_hash() -> None:
    body = {"event_type": "X", "payload": {}}
    assert compute_hash(body, "") != compute_hash(body, "abc")


# --- Chainage -------------------------------------------------------------


async def test_first_entry_prev_hash_is_empty(project_db: Path) -> None:
    await make_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            entry = await append(conn, 1, AuditEventType.PROJECT_CREATED, {"nom": "These"}, tx=True)
    assert entry.prev_hash == ""
    assert len(entry.hash) == 64


async def test_chain_valid_over_500_entries(project_db: Path) -> None:
    await make_db(project_db)
    await append_n(project_db, 500)
    async with connect(project_db) as conn:
        result = await verify_chain(conn, 1)
    assert result.status is ChainStatus.VALIDE
    assert result.entries_checked == 500
    assert result.first_invalid_index is None


async def test_empty_journal_is_valid(project_db: Path) -> None:
    await make_db(project_db)
    async with connect(project_db) as conn:
        result = await verify_chain(conn, 1)
    assert result.status is ChainStatus.VALIDE
    assert result.entries_checked == 0


async def test_tamper_detection_returns_index_and_id(project_db: Path) -> None:
    await make_db(project_db)
    await append_n(project_db, 20)

    # Alteration directe en SQL : exactement ce que peut faire le proprietaire
    # du fichier. La chaine ne l'empeche pas, elle le rend detectable.
    con = sqlite3.connect(project_db)
    try:
        cible = con.execute("SELECT id FROM audit_log ORDER BY id LIMIT 1 OFFSET 9").fetchone()[0]
        con.execute(
            "UPDATE audit_log SET payload_json = ? WHERE id = ?",
            ('{"index":999,"to":"FALSIFIE"}', cible),
        )
        con.commit()
    finally:
        con.close()

    async with connect(project_db) as conn:
        result = await verify_chain(conn, 1)
    assert result.status is ChainStatus.ALTERE
    assert result.first_invalid_index == 9
    assert result.first_invalid_id == cible


async def test_tamper_on_deleted_entry_is_detected(project_db: Path) -> None:
    """Une entree retiree rompt le chainage des suivantes."""
    await make_db(project_db)
    await append_n(project_db, 12)
    con = sqlite3.connect(project_db)
    try:
        cible = con.execute("SELECT id FROM audit_log ORDER BY id LIMIT 1 OFFSET 5").fetchone()[0]
        con.execute("DELETE FROM audit_log WHERE id = ?", (cible,))
        con.commit()
    finally:
        con.close()

    async with connect(project_db) as conn:
        result = await verify_chain(conn, 1)
    assert result.status is ChainStatus.ALTERE
    assert result.first_invalid_index == 5


async def test_verify_is_read_only(project_db: Path) -> None:
    await make_db(project_db)
    await append_n(project_db, 30)
    con = sqlite3.connect(project_db)
    try:
        avant = con.execute("SELECT id, hash, payload_json FROM audit_log ORDER BY id").fetchall()
    finally:
        con.close()

    async with connect(project_db) as conn:
        await verify_chain(conn, 1)

    con = sqlite3.connect(project_db)
    try:
        apres = con.execute("SELECT id, hash, payload_json FROM audit_log ORDER BY id").fetchall()
    finally:
        con.close()
    assert avant == apres


async def test_verify_batches_without_loading_all(
    project_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le parcours se fait par lots : une these produit des dizaines de
    milliers d'entrees, les charger toutes ferait echouer la verification la
    ou elle sert le plus."""
    await make_db(project_db)
    await append_n(project_db, 25)
    monkeypatch.setattr(audit_service, "BATCH_SIZE", 10)

    tailles: list[int] = []
    original = aiosqlite.Cursor.fetchall

    async def compter(self: aiosqlite.Cursor):  # type: ignore[no-untyped-def]
        rows = await original(self)
        tailles.append(len(rows))
        return rows

    monkeypatch.setattr(aiosqlite.Cursor, "fetchall", compter)
    async with connect(project_db) as conn:
        result = await verify_chain(conn, 1)

    assert result.status is ChainStatus.VALIDE
    assert result.entries_checked == 25
    assert max(tailles) <= 10, f"un lot a depasse la taille demandee : {tailles}"


# --- Chemin d'insertion unique --------------------------------------------


async def test_append_requires_open_transaction(project_db: Path) -> None:
    await make_db(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(AuditWriteError, match="tx=True"):
            await append(conn, 1, AuditEventType.LLM_CALL, {"agent": "plan"}, tx=False)


async def test_append_refuses_when_no_transaction_actually_open(project_db: Path) -> None:
    """`tx=True` ne suffit pas : la connexion doit reellement etre en transaction."""
    await make_db(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(AuditWriteError, match="Aucune transaction"):
            await append(conn, 1, AuditEventType.LLM_CALL, {"agent": "plan"}, tx=True)


async def test_concurrent_append_serialized_by_lock(project_db: Path) -> None:
    """Sans serialisation, deux coroutines liraient le meme prev_hash."""
    await make_db(project_db)

    async def writer(i: int) -> None:
        async with connect(project_db) as conn:
            async with transaction(conn):
                await append(conn, 1, AuditEventType.LLM_CALL, {"i": i}, tx=True)

    await asyncio.gather(*(writer(i) for i in range(15)))

    async with connect(project_db) as conn:
        result = await verify_chain(conn, 1)
    assert result.status is ChainStatus.VALIDE
    assert result.entries_checked == 15


async def test_duplicate_prev_hash_violates_unique_index(project_db: Path) -> None:
    """La fourche devient une violation de contrainte, pas une corruption discrete."""
    await make_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            premiere = await append(conn, 1, AuditEventType.PROJECT_CREATED, {}, tx=True)
        with pytest.raises(aiosqlite.IntegrityError, match="UNIQUE"):
            async with transaction(conn):
                await conn.execute(
                    "INSERT INTO audit_log (project_id, event_type, payload_json, prev_hash,"
                    " hash, created_at) VALUES (1, 'LLM_CALL', '{}', ?, 'fourche', ?)",
                    (premiere.prev_hash, premiere.created_at),
                )


# --- Contenu des evenements ----------------------------------------------


async def test_llm_call_does_not_store_full_prompt(project_db: Path) -> None:
    """Le journal doit rester consultable sur une these entiere."""
    await make_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await append(
                conn,
                1,
                AuditEventType.LLM_CALL,
                {
                    "agent": "writer",
                    "model": "qwen2.5:7b-instruct-q4_K_M",
                    "prompt_version": "a1b2c3d4e5f6",
                    "prompt_tokens": 1200,
                    "completion_tokens": 800,
                    "section_id": 12,
                },
                tx=True,
            )
        entries = await list_entries(conn, 1)

    payload = entries[0].payload
    assert set(payload) == {
        "agent",
        "model",
        "prompt_version",
        "prompt_tokens",
        "completion_tokens",
        "section_id",
    }
    assert "prompt" not in payload
    assert "response" not in payload


async def test_guardrail_payload_truncated_to_2kb(project_db: Path) -> None:
    await make_db(project_db)
    long_output = "x" * 10_000
    async with connect(project_db) as conn:
        async with transaction(conn):
            await append(
                conn,
                1,
                AuditEventType.GUARDRAIL_TRIGGERED,
                {"rule": "citation_inconnue", "output": long_output},
                tx=True,
            )
        entries = await list_entries(conn, 1)

    stocke = entries[0].payload["output"]
    assert len(stocke.encode("utf-8")) == 2048
    assert entries[0].payload["output_truncated"] is True


async def test_list_entries_filters_by_event_type(project_db: Path) -> None:
    await make_db(project_db)
    async with connect(project_db) as conn:
        for event in (
            AuditEventType.PROJECT_CREATED,
            AuditEventType.LLM_CALL,
            AuditEventType.LLM_CALL,
        ):
            async with transaction(conn):
                await append(conn, 1, event, {}, tx=True)
        toutes = await list_entries(conn, 1)
        llm = await list_entries(conn, 1, event_type="LLM_CALL")
    assert len(toutes) == 3
    assert len(llm) == 2
