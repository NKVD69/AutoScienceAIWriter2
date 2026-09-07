"""Journal à détection d'altération — sérialisation, chaînage, vérification.

ADR-009. Ce module est le **seul** chemin d'écriture vers `audit_log`. Aucun
`UPDATE` ni `DELETE` n'y figure, et un test parcourt l'AST de `backend/app/`
pour vérifier qu'il n'en existe nulle part ailleurs : une convention non
vérifiée n'est pas une convention.

La chaîne apporte une **détection d'altération**, pas une immutabilité :
l'utilisateur possède le fichier et peut le réécrire entièrement. C'est
précisément pourquoi le vocabulaire est contraint (D-07).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from app.core.errors import AppError
from app.models.audit import AuditEntry, AuditEventType, ChainStatus, ChainVerification

# Taille de lot de vérification. Une thèse produit des dizaines de milliers
# d'entrées : les charger toutes en mémoire pour vérifier ferait échouer la
# vérification là où elle est le plus utile — sur un journal volumineux.
BATCH_SIZE = 1000

# Tronquage de la sortie fautive conservée par GUARDRAIL_TRIGGERED.
GUARDRAIL_PAYLOAD_MAX_BYTES = 2048

# Verrous par projet : lire le dernier hash puis insérer n'est atomique que
# si la séquence est sérialisée.
_locks: dict[int | None, asyncio.Lock] = {}


class AuditWriteError(AppError):
    """Écriture d'audit impossible. Fait échouer l'opération métier décrite."""

    code = "AUDIT_WRITE_FAILED"
    status_code = 500


def _lock_for(project_id: int | None) -> asyncio.Lock:
    lock = _locks.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[project_id] = lock
    return lock


def canonical(entry: dict[str, Any]) -> bytes:
    """Sérialisation canonique d'une entrée.

    ATTENTION — FORMAT FIGÉ. Toute évolution de cette fonction invalide
    toutes les chaînes déjà écrites : une vérification faite dans six mois
    doit produire exactement le même condensé qu'aujourd'hui. Un changement
    ici exige donc une migration de chaîne, jamais une simple correction.

    Règles : clés triées, séparateurs sans espace, UTF-8 sans échappement
    ASCII, NaN et Infinity refusés à l'écriture.
    """
    _reject_non_finite(entry)
    return json.dumps(
        entry,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _reject_non_finite(value: Any) -> None:
    """Refuse NaN et Infinity, que `json` sérialiserait en littéraux non standard.

    Un `NaN` inscrit dans le journal produirait un JSON que d'autres lecteurs
    refusent : la chaîne deviendrait invérifiable par un tiers, ce qui est
    exactement ce qu'on cherche à éviter.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN et Infinity sont refusés dans une entrée d'audit.")
    elif isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def compute_hash(entry: dict[str, Any], prev_hash: str) -> str:
    """`sha256(canonical(entry) || prev_hash)`."""
    digest = hashlib.sha256()
    digest.update(canonical(entry))
    digest.update(prev_hash.encode("utf-8"))
    return digest.hexdigest()


def _entry_body(
    project_id: int | None, event_type: str, payload: dict[str, Any], created_at: str
) -> dict[str, Any]:
    """Corps couvert par le condensé. Les champs de chaînage en sont exclus :
    ils sont mélangés séparément, sinon le hash se référencerait lui-même."""
    return {
        "project_id": project_id,
        "event_type": event_type,
        "payload": payload,
        "created_at": created_at,
    }


def truncate_guardrail_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Tronque la sortie fautive d'un guardrail à 2 Ko."""
    output = payload.get("output")
    if not isinstance(output, str):
        return payload
    encoded = output.encode("utf-8")
    if len(encoded) <= GUARDRAIL_PAYLOAD_MAX_BYTES:
        return payload
    trimmed = encoded[:GUARDRAIL_PAYLOAD_MAX_BYTES].decode("utf-8", errors="ignore")
    return {**payload, "output": trimmed, "output_truncated": True}


async def _last_hash(conn: aiosqlite.Connection, project_id: int | None) -> str:
    if project_id is None:
        sql = "SELECT hash FROM audit_log WHERE project_id IS NULL ORDER BY id DESC LIMIT 1"
        args: tuple[Any, ...] = ()
    else:
        sql = "SELECT hash FROM audit_log WHERE project_id = ? ORDER BY id DESC LIMIT 1"
        args = (project_id,)
    async with conn.execute(sql, args) as cur:
        row = await cur.fetchone()
    # La première entrée d'un projet chaîne sur la chaîne vide.
    return str(row[0]) if row else ""


async def append(
    conn: aiosqlite.Connection,
    project_id: int | None,
    event_type: AuditEventType | str,
    payload: dict[str, Any],
    *,
    tx: bool,
) -> AuditEntry:
    """Écrit une entrée. Seule fonction autorisée à écrire dans `audit_log`.

    `tx` atteste qu'une transaction est **déjà ouverte** : l'entrée doit être
    inscrite dans la même transaction que l'opération métier qu'elle décrit.
    Séparées, une opération pourrait réussir sans être journalisée, ou être
    journalisée sans avoir eu lieu — les deux ruinent la valeur probante du
    journal. Conséquence assumée : un échec d'écriture d'audit fait échouer
    l'opération métier.
    """
    if not tx:
        raise AuditWriteError(
            "append() exige une transaction déjà ouverte (paramètre tx=True). "
            "L'entrée d'audit doit partager la transaction de l'opération métier "
            "qu'elle décrit (ADR-009)."
        )
    if not conn.in_transaction:
        raise AuditWriteError(
            "Aucune transaction ouverte sur la connexion : l'entrée d'audit "
            "serait validée indépendamment de l'opération métier."
        )

    event = str(event_type)
    body_payload = (
        truncate_guardrail_payload(payload)
        if event == AuditEventType.GUARDRAIL_TRIGGERED
        else payload
    )
    created_at = datetime.now(UTC).isoformat()

    async with _lock_for(project_id):
        prev_hash = await _last_hash(conn, project_id)
        body = _entry_body(project_id, event, body_payload, created_at)
        digest = compute_hash(body, prev_hash)
        cur = await conn.execute(
            "INSERT INTO audit_log (project_id, event_type, payload_json, prev_hash, hash,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                project_id,
                event,
                json.dumps(body_payload, sort_keys=True, ensure_ascii=False, allow_nan=False),
                prev_hash,
                digest,
                created_at,
            ),
        )
        entry_id = cur.lastrowid

    return AuditEntry(
        id=int(entry_id or 0),
        project_id=project_id,
        event_type=event,
        payload=body_payload,
        prev_hash=prev_hash,
        hash=digest,
        created_at=created_at,
    )


async def _iter_batches(
    conn: aiosqlite.Connection, project_id: int
) -> AsyncIterator[list[aiosqlite.Row]]:
    """Parcourt le journal par lots, sans jamais le charger en entier."""
    last_id = 0
    while True:
        async with conn.execute(
            "SELECT id, project_id, event_type, payload_json, prev_hash, hash, created_at"
            " FROM audit_log WHERE project_id = ? AND id > ? ORDER BY id LIMIT ?",
            (project_id, last_id, BATCH_SIZE),
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return
        yield rows
        last_id = int(rows[-1][0])


async def verify_chain(conn: aiosqlite.Connection, project_id: int) -> ChainVerification:
    """Recalcule la chaîne. Lecture seule : ne modifie jamais le journal."""
    expected_prev = ""
    index = 0

    async for rows in _iter_batches(conn, project_id):
        for row in rows:
            payload = json.loads(row[3])
            body = _entry_body(row[1], str(row[2]), payload, str(row[6]))
            recomputed = compute_hash(body, str(row[4]))
            if str(row[4]) != expected_prev or recomputed != str(row[5]):
                return ChainVerification(
                    status=ChainStatus.ALTERE,
                    entries_checked=index + 1,
                    first_invalid_index=index,
                    first_invalid_id=int(row[0]),
                )
            expected_prev = str(row[5])
            index += 1

    # Un journal vide est valide : rien n'a été altéré.
    return ChainVerification(status=ChainStatus.VALIDE, entries_checked=index)


async def list_entries(
    conn: aiosqlite.Connection,
    project_id: int,
    event_type: str | None = None,
    limit: int = 100,
) -> list[AuditEntry]:
    sql = (
        "SELECT id, project_id, event_type, payload_json, prev_hash, hash, created_at"
        " FROM audit_log WHERE project_id = ?"
    )
    args: list[Any] = [project_id]
    if event_type:
        sql += " AND event_type = ?"
        args.append(event_type)
    sql += " ORDER BY id LIMIT ?"
    args.append(limit)

    async with conn.execute(sql, args) as cur:
        rows = await cur.fetchall()
    return [
        AuditEntry(
            id=int(r[0]),
            project_id=r[1],
            event_type=str(r[2]),
            payload=json.loads(r[3]),
            prev_hash=str(r[4]),
            hash=str(r[5]),
            created_at=str(r[6]),
        )
        for r in rows
    ]
