"""Agent factice et projet de test — US-201, point 8.

Aucun test de cette story n'exige un moteur reel. Les sorties fautives sont
INJECTEES : un modele de 31B produit du JSON conforme la plupart du temps,
ce qui rend ses echecs rares et donc impossibles a provoquer autrement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
from pydantic import BaseModel, Field

from app.agents.state import GraphState, initial_state
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.llm.prompts.registry import AgentName
from app.services import task_service

NOW = datetime.now(UTC).isoformat()


class EchoOutput(BaseModel):
    """Modele de sortie simple, avec des contraintes reellement violables."""

    titre: str = Field(min_length=3, max_length=80)
    points: list[str] = Field(min_length=1, max_length=3)
    poids: int = Field(ge=1, le=100)


VALID = '{"titre": "Un titre correct", "points": ["a", "b"], "poids": 42}'
VALID_WRAPPED = (
    "Voici le plan demande :\n```json\n"
    '{"titre": "Titre enrobe", "points": ["a"], "poids": 7}\n'
    "```\nJ'espere que cela convient."
)
MALFORMED = '{"titre": "Sans accolade fermante", "points": ["a"], "poids": 1'
NO_JSON = "Je ne peux pas produire ce plan."
CONSTRAINT_VIOLATED = '{"titre": "ab", "points": [], "poids": 500}'
OVERSIZED_LIST = '{"titre": "Trop de points", "points": ["a","b","c","d","e"], "poids": 5}'
MISSING_FIELD = '{"titre": "Il manque le poids", "points": ["a"]}'
BRACE_IN_STRING = '{"titre": "Une {accolade} dans un titre", "points": ["a"], "poids": 3}'


class EchoAgent:
    """Agent alimente par une file de reponses preenregistrees."""

    name = AgentName.PLAN
    output_model = EchoOutput

    def __init__(self, reponses: list[str]) -> None:
        self.reponses = list(reponses)
        self.appels: list[str] = []

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        return f"projet {state['project_id']} · essai {state['retry_count'] + 1}"

    async def run(self, state: GraphState, ctx: dict) -> str:
        message = self.build_user_message(state, ctx)
        self.appels.append(message)
        # La derniere reponse se repete : modelise un modele qui echoue
        # indefiniment, ce que le circuit breaker doit arreter.
        return self.reponses.pop(0) if len(self.reponses) > 1 else self.reponses[0]


class ReviewerSpy:
    """Relecteur qui ne doit JAMAIS etre appele par un rejet de guardrail."""

    name = AgentName.REVIEWER
    output_model = EchoOutput

    def __init__(self) -> None:
        self.appels = 0

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        return "relecture"

    async def run(self, state: GraphState, ctx: dict) -> str:
        self.appels += 1
        return VALID


@pytest.fixture
def echo_agent() -> EchoAgent:
    return EchoAgent([VALID])


@pytest.fixture
def reviewer() -> ReviewerSpy:
    return ReviewerSpy()


@pytest.fixture
async def projet(tmp_path: Path):
    """Fichier projet migre, avec une ligne `project`. Rend (chemin, conn)."""
    task_service.reset_queues()
    chemin = tmp_path / "projet.sqlite"
    async with connect(chemin) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'P','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
        yield chemin, conn
    task_service.reset_queues()


@pytest.fixture
def etat() -> GraphState:
    return initial_state(project_id=1)


async def count_audit(conn: aiosqlite.Connection, event_type: str) -> int:
    async with conn.execute(
        "SELECT count(*) FROM audit_log WHERE event_type = ?", (event_type,)
    ) as cur:
        return int((await cur.fetchone())[0])
