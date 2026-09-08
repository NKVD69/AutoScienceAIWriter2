"""US-201 — le guardrail valide ou rejette. Il ne repare jamais. ADR-008."""

from __future__ import annotations

import pytest

from app.agents.guardrails import extract_json, validate_output

from .conftest import (
    BRACE_IN_STRING,
    CONSTRAINT_VIOLATED,
    MALFORMED,
    MISSING_FIELD,
    NO_JSON,
    OVERSIZED_LIST,
    VALID,
    VALID_WRAPPED,
    EchoAgent,
    EchoOutput,
    ReviewerSpy,
)

# --- Extraction : tolerance de FORMAT -------------------------------------


def test_extract_json_from_bare_object() -> None:
    assert extract_json(VALID) == VALID


def test_guardrail_extracts_json_wrapped_in_text() -> None:
    """Un modele qui entoure son JSON de prose reste comprehensible."""
    fragment = extract_json(VALID_WRAPPED)
    assert fragment.startswith("{") and fragment.endswith("}")
    assert "J'espere" not in fragment


def test_extract_json_tolerates_brace_inside_a_string() -> None:
    """Une accolade dans un titre ne doit pas fermer l'objet prematurement."""
    assert extract_json(BRACE_IN_STRING) == BRACE_IN_STRING


def test_extract_json_reports_absence() -> None:
    with pytest.raises(ValueError, match="aucun objet JSON"):
        extract_json(NO_JSON)


def test_extract_json_reports_unclosed_object() -> None:
    with pytest.raises(ValueError, match="non refermé"):
        extract_json(MALFORMED)


# --- Validation -----------------------------------------------------------


async def test_valid_output_is_parsed() -> None:
    resultat = await validate_output(VALID, EchoOutput)
    assert resultat.ok
    assert resultat.parsed is not None
    assert resultat.parsed.titre == "Un titre correct"
    assert resultat.error_message is None


async def test_wrapped_output_is_parsed() -> None:
    resultat = await validate_output(VALID_WRAPPED, EchoOutput)
    assert resultat.ok
    assert resultat.parsed.titre == "Titre enrobe"


async def test_no_json_is_rejected_with_instruction() -> None:
    resultat = await validate_output(NO_JSON, EchoOutput)
    assert not resultat.ok
    assert resultat.error_path == "(format)"
    assert "UNIQUEMENT" in resultat.error_message


async def test_malformed_json_is_rejected_not_repaired() -> None:
    """Recoller un JSON par expression reguliere masquerait le defaut."""
    resultat = await validate_output(MALFORMED, EchoOutput)
    assert not resultat.ok
    assert resultat.parsed is None


async def test_guardrail_reports_field_path_and_constraint() -> None:
    """Le message est destine au MODELE : il doit etre actionnable."""
    resultat = await validate_output(CONSTRAINT_VIOLATED, EchoOutput)
    assert not resultat.ok
    assert resultat.error_path in {"titre", "points", "poids"}
    message = resultat.error_message
    assert "titre" in message and "points" in message and "poids" in message
    assert "3" in message, "la contrainte violée doit être nommée"
    assert "Corrige" in message


async def test_guardrail_truncates_the_received_value() -> None:
    """Une valeur immense encombrerait le prompt de correction."""
    enorme = '{"titre": "' + "x" * 5000 + '", "points": ["a"], "poids": 1}'
    resultat = await validate_output(enorme, EchoOutput)
    assert not resultat.ok
    assert len(resultat.error_message) < 2000
    assert "…" in resultat.error_message


# --- Interdictions formelles ----------------------------------------------


async def test_guardrail_never_fills_missing_field() -> None:
    """Completer un champ absent produirait une donnee silencieusement fausse."""
    resultat = await validate_output(MISSING_FIELD, EchoOutput)
    assert not resultat.ok
    assert resultat.parsed is None
    assert "poids" in resultat.error_message


async def test_guardrail_never_truncates_oversized_list() -> None:
    """Tronquer pour faire passer masquerait un plan mal construit."""
    resultat = await validate_output(OVERSIZED_LIST, EchoOutput)
    assert not resultat.ok
    assert resultat.parsed is None
    assert "points" in resultat.error_message


async def test_guardrail_source_contains_no_repair_logic() -> None:
    """Aucune reparation par expression reguliere dans le module."""
    import inspect

    from app.agents import guardrails

    source = inspect.getsource(guardrails)
    for interdit in ("re.sub", "re.compile", "setdefault", "[:3]"):
        assert interdit not in source, f"trace de réparation : {interdit}"


# --- Le guardrail relance le MEME agent -----------------------------------


async def test_guardrail_reruns_same_agent_not_reviewer(
    echo_agent: EchoAgent, reviewer: ReviewerSpy, etat
) -> None:
    """Une erreur de format n'est pas un probleme de fond : le relecteur
    n'a rien a y faire."""
    from app.agents.breaker import register_guardrail_failure

    agent = EchoAgent([CONSTRAINT_VIOLATED, CONSTRAINT_VIOLATED, VALID])

    for _ in range(3):
        brut = await agent.run(etat, {})
        resultat = await validate_output(brut, EchoOutput)
        if resultat.ok:
            break
        decision = register_guardrail_failure(etat, resultat.error_message)
        if decision.tripped:
            break

    assert len(agent.appels) >= 2, "le même agent doit être relancé"
    assert reviewer.appels == 0, "le relecteur ne doit jamais être appelé sur un rejet de format"
