"""US-201/202 — reprise apres arret et sortie d'ERROR_STATE."""

from __future__ import annotations

import pytest

from app.agents.state import WorkflowState, initial_state
from app.db.session import connect, transaction
from app.services import workflow_service
from app.services.workflow_service import NotInErrorStateError, ResumeAction

from .conftest import count_audit


async def _en_erreur(conn, project_id: int = 1) -> tuple[int, dict]:
    """Tache conduite jusqu'a ERROR_STATE, etat d'origine memorise."""
    task_id = await workflow_service.start(conn, project_id=project_id)
    etat = initial_state(project_id)
    etat["state"] = WorkflowState.PLAN_GUARDRAIL
    workflow_service.remember_state_before_error(etat)
    etat["retry_count"] = 3
    etat["review_loop_count"] = 2
    await workflow_service.fail(conn, task_id, etat, "trois rejets consécutifs")
    return task_id, etat


# --- Reprise apres arret du backend ---------------------------------------


async def test_resume_after_restart_continues_from_persisted_state(projet) -> None:
    """Une tache interrompue reprend au nœud, pas au milieu de l'appel."""
    chemin, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)

    etat = initial_state(1)
    etat = await workflow_service.transition(conn, task_id, etat, WorkflowState.SOURCES_INGESTING)
    etat["payload_json"]["sources_traitees"] = 37

    async with transaction(conn):
        await workflow_service.save_state(conn, task_id, etat)

    # Arret du backend : nouvelle connexion, plus rien en memoire.
    async with connect(chemin) as apres_redemarrage:
        relu = await workflow_service.load_state(apres_redemarrage, task_id)

    assert relu is not None
    assert relu["state"] is WorkflowState.SOURCES_INGESTING
    assert relu["payload_json"]["sources_traitees"] == 37


async def test_resumable_tasks_lists_only_non_terminal(projet) -> None:
    _, conn = projet
    en_cours = await workflow_service.start(conn, project_id=1)
    terminee = await workflow_service.start(conn, project_id=1)

    etat = initial_state(1)
    etat["state"] = WorkflowState.EXPORTING
    await workflow_service.transition(conn, terminee, etat, WorkflowState.EXPORTED)

    a_reprendre = await workflow_service.resumable_tasks(conn)
    assert en_cours in a_reprendre
    assert terminee not in a_reprendre


async def test_error_state_task_is_not_resumed_automatically(projet) -> None:
    """ERROR_STATE est terminal : il ne figure pas dans les taches a reprendre."""
    _, conn = projet
    task_id, _ = await _en_erreur(conn)
    assert task_id not in await workflow_service.resumable_tasks(conn)


# --- Sortie d'ERROR_STATE -------------------------------------------------


async def test_resume_from_error_retry_returns_to_previous_state(projet) -> None:
    _, conn = projet
    task_id, _ = await _en_erreur(conn)

    repris = await workflow_service.resume_from_error(conn, task_id, ResumeAction.RETRY)
    assert repris["state"] is WorkflowState.PLAN_GUARDRAIL
    # Les compteurs sont remis a zero : sinon le breaker redeclencherait
    # immediatement et la reprise serait sans effet.
    assert repris["retry_count"] == 0
    assert repris["review_loop_count"] == 0
    assert repris["last_error"] is None


async def test_resume_from_error_skip_also_leaves_error_state(projet) -> None:
    _, conn = projet
    task_id, _ = await _en_erreur(conn)
    repris = await workflow_service.resume_from_error(conn, task_id, ResumeAction.SKIP)
    assert repris["state"] is not WorkflowState.ERROR_STATE


async def test_resume_from_error_abort_stays_in_error(projet) -> None:
    """L'utilisateur a decide d'arreter : on ne le contredit pas."""
    _, conn = projet
    task_id, _ = await _en_erreur(conn)
    repris = await workflow_service.resume_from_error(conn, task_id, ResumeAction.ABORT)
    assert repris["state"] is WorkflowState.ERROR_STATE

    relu = await workflow_service.load_state(conn, task_id)
    assert relu["state"] is WorkflowState.ERROR_STATE


async def test_resume_is_persisted_and_audited(projet) -> None:
    _, conn = projet
    task_id, _ = await _en_erreur(conn)
    avant = await count_audit(conn, "STATE_TRANSITION")

    await workflow_service.resume_from_error(conn, task_id, ResumeAction.RETRY)

    relu = await workflow_service.load_state(conn, task_id)
    assert relu["state"] is WorkflowState.PLAN_GUARDRAIL
    assert await count_audit(conn, "STATE_TRANSITION") == avant + 1
    assert await count_audit(conn, "HUMAN_VALIDATION") >= 1


async def test_resume_refused_when_not_in_error(projet) -> None:
    """Il n'y a rien a reprendre sur une tache qui avance normalement."""
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    with pytest.raises(NotInErrorStateError, match="PROJECT_CREATED"):
        await workflow_service.resume_from_error(conn, task_id, ResumeAction.RETRY)


async def test_resume_refused_on_unknown_task(projet) -> None:
    _, conn = projet
    with pytest.raises(NotInErrorStateError, match="inconnue"):
        await workflow_service.resume_from_error(conn, 4242, ResumeAction.RETRY)


async def test_only_resume_leaves_error_state(projet) -> None:
    """Aucune transition declaree ne sort d'ERROR_STATE (S5.3)."""
    from app.agents.state import allowed_targets
    from app.core.errors import InvalidTransitionError

    _, conn = projet
    task_id, _ = await _en_erreur(conn)
    etat = await workflow_service.load_state(conn, task_id)

    assert allowed_targets(WorkflowState.ERROR_STATE) == frozenset()
    for cible in WorkflowState:
        with pytest.raises(InvalidTransitionError):
            await workflow_service.transition(conn, task_id, dict(etat), cible)
