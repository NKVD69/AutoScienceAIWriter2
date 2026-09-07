"""US-701 — l'audit partage la transaction de l'operation metier. ADR-009 S3."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.models.audit import AuditEventType, ChainStatus
from app.services.audit_service import append, list_entries, verify_chain

NOW = datetime.now(UTC).isoformat()


async def make_db(db_path: Path) -> None:
    async with connect(db_path) as conn:
        await run_migrations(conn)


async def creer_projet(conn, nom: str = "These") -> int:
    cur = await conn.execute(
        "INSERT INTO project (name, subject, language, academic_level, created_at, updated_at)"
        " VALUES (?, 'Sujet', 'fr', 'doctorat', ?, ?)",
        (nom, NOW, NOW),
    )
    return int(cur.lastrowid)


async def test_audit_written_in_same_transaction_as_business_op(project_db: Path) -> None:
    await make_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            project_id = await creer_projet(conn)
            await append(
                conn, project_id, AuditEventType.PROJECT_CREATED, {"nom": "These"}, tx=True
            )

        async with conn.execute("SELECT count(*) FROM project") as cur:
            projets = (await cur.fetchone())[0]
        entries = await list_entries(conn, project_id)

    assert projets == 1
    assert len(entries) == 1
    assert entries[0].event_type == "PROJECT_CREATED"


async def test_rolled_back_operation_leaves_no_audit_entry(project_db: Path) -> None:
    """Une transition annulee n'est pas journalisee : c'est la propriete
    recherchee — une transition journalisee a necessairement eu lieu."""
    await make_db(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(RuntimeError):
            async with transaction(conn):
                project_id = await creer_projet(conn)
                await append(conn, project_id, AuditEventType.PROJECT_CREATED, {}, tx=True)
                raise RuntimeError("echec metier apres journalisation")

        async with conn.execute("SELECT count(*) FROM project") as cur:
            projets = (await cur.fetchone())[0]
        async with conn.execute("SELECT count(*) FROM audit_log") as cur:
            entrees = (await cur.fetchone())[0]

    assert projets == 0
    assert entrees == 0


async def test_audit_failure_fails_business_operation(project_db: Path) -> None:
    """Consequence assumee : un audit impossible annule l'operation decrite."""
    await make_db(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(ValueError, match="NaN"):
            async with transaction(conn):
                await creer_projet(conn, "Projet fantome")
                await append(conn, 1, AuditEventType.LLM_CALL, {"latence": float("nan")}, tx=True)

        async with conn.execute("SELECT count(*) FROM project WHERE name='Projet fantome'") as cur:
            projets = (await cur.fetchone())[0]
    assert projets == 0


async def test_chain_survives_a_realistic_sequence(project_db: Path) -> None:
    """Sequence proche d'un usage reel : creation, import, transition, appel LLM."""
    await make_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            project_id = await creer_projet(conn)
            await append(
                conn, project_id, AuditEventType.PROJECT_CREATED, {"nom": "These"}, tx=True
            )
        for event, payload in (
            (AuditEventType.SOURCE_IMPORTED, {"source_id": 1, "titre": "Article A"}),
            (AuditEventType.SOURCE_APPROVED, {"source_id": 1}),
            (AuditEventType.INGESTION_STARTED, {"sources": 1}),
            (AuditEventType.INGESTION_COMPLETED, {"chunks": 128}),
            (AuditEventType.STATE_TRANSITION, {"de": "SOURCES_READY", "vers": "PLAN_DRAFTING"}),
            (AuditEventType.LLM_CALL, {"agent": "plan", "prompt_tokens": 900}),
            (AuditEventType.HUMAN_VALIDATION, {"porte": "PLAN_REVIEW", "decision": "validee"}),
        ):
            async with transaction(conn):
                await append(conn, project_id, event, payload, tx=True)

        result = await verify_chain(conn, project_id)
        entries = await list_entries(conn, project_id, limit=100)

    assert result.status is ChainStatus.VALIDE
    assert result.entries_checked == 8
    assert [e.event_type for e in entries][:2] == ["PROJECT_CREATED", "SOURCE_IMPORTED"]
    # Chaque entree chaine sur la precedente.
    for precedente, suivante in pairwise(entries):
        assert suivante.prev_hash == precedente.hash


async def test_two_projects_have_independent_chains(project_db: Path) -> None:
    await make_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            a = await creer_projet(conn, "A")
            b = await creer_projet(conn, "B")
        for pid in (a, b):
            async with transaction(conn):
                await append(conn, pid, AuditEventType.PROJECT_CREATED, {"id": pid}, tx=True)
        premiere_a = (await list_entries(conn, a))[0]
        premiere_b = (await list_entries(conn, b))[0]
        assert (await verify_chain(conn, a)).status is ChainStatus.VALIDE
        assert (await verify_chain(conn, b)).status is ChainStatus.VALIDE

    # Deux projets demarrent chacun sur la chaine vide sans se gener.
    assert premiere_a.prev_hash == ""
    assert premiere_b.prev_hash == ""
    assert premiere_a.hash != premiere_b.hash
