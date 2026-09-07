"""US-001 — `/health` conforme au schéma `Health` du contrat."""

from __future__ import annotations

import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def client():
    # Transport ASGI direct, sans serveur ni dépendance de test supplémentaire.
    # Le cycle de vie n'est pas déclenché : `/health` n'en dépend pas, et la
    # sonde base ouvre son propre fichier temporaire.
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c


async def test_health_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "version", "database", "llm"}
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    # US-003 n'est pas livrée : annoncer `ok` serait annoncer une capacité absente.
    assert body["llm"] == "unavailable"


async def test_health_is_mounted_under_api_prefix(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 404
