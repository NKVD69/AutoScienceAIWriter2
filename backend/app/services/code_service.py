"""Proposition, exécution et rapprochement du code d'analyse — US-401, §8.

**Tout code d'agent s'exécute au niveau 1, sans exception.** `factory.select`
reçoit `origin=AGENT` : un agent ne choisit jamais son niveau d'isolation
(US-004). Le niveau 2 relève d'une décision humaine, hors de ce service.

**Un jeu de données inconnu est refusé AVANT l'exécution**, pas découvert dans
une traceback : le contrôle précède le lancement du runtime.

**Le rapprochement attendu/produit décide de la suite.** Un fichier attendu mais
absent relance l'agent avec un message précis ; un fichier produit mais non
déclaré est conservé et signalé, jamais rattaché seul. La boucle de correction
est plafonnée (US-202) ; passé le plafond, `ERROR_STATE`.

**Une indisponibilité de paquet ne relance PAS l'agent.** `PackageUnavailableInWasmError`
remonte à l'utilisateur avec la proposition du niveau 2 : c'est une décision, pas
une erreur de code.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import aiosqlite

from app.agents.code_agent import CodeAgent
from app.agents.guardrails import validate_output
from app.agents.state import GraphState, WorkflowState, initial_state
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.manager import LLMManager
from app.models.code import (
    CodeExecutionOut,
    CodeProposal,
    ExecutionResultOut,
    ExpectedArtifact,
)
from app.sandbox import factory
from app.sandbox.base import MountSpec, ResourceLimits, SandboxMode, SandboxOrigin
from app.services import artifact_service, task_service

logger = get_logger(__name__)

# Plafond de la boucle de correction (US-202). Au-delà : ERROR_STATE.
MAX_CODE_CORRECTIONS = 3
# On ne transmet au modèle que la fin de stderr : la traceback entière noierait
# le motif sous des cadres de pile sans intérêt (US-401, point 4).
STDERR_TAIL_LINES = 20

_LIMIT_LABEL = {
    "memory": "dépassement de la mémoire autorisée",
    "cpu": "dépassement du temps CPU autorisé",
    "wall": "dépassement du délai mural",
}


class UnknownDatasetError(AppError):
    """Un jeu de données cité n'existe pas dans le projet.

    Refusé avant l'exécution : l'agent ne doit pas apprendre par une traceback
    qu'un fichier qu'il croyait monté n'existe pas.
    """

    code = "UNKNOWN_DATASET"
    status_code = 422


class CodeExecutionError(AppError):
    """La production a échoué après le plafond d'essais (US-202).

    Rendre un résultat malgré tout reviendrait à livrer une figure qu'on sait
    fausse ou absente. On s'arrête et on attend l'humain (ERROR_STATE).
    """

    code = "CODE_EXECUTION_FAILED"
    status_code = 422


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")


def _artifacts_root(project_id: int) -> Path:
    return get_settings().data_dir / "artifacts" / str(project_id)


# --- Jeux de données ------------------------------------------------------


def resolve_datasets(
    project_id: int, names: list[str], known: dict[str, Path]
) -> list[tuple[str, Path, PurePosixPath]]:
    """Résout les noms en (nom, chemin hôte, chemin invité). Refuse l'inconnu.

    `known` est le registre des jeux déposés (US-DATA-001). Tant qu'il n'existe
    pas, il est vide : tout nom est alors inconnu, et l'on refuse avant d'exécuter.
    """
    resolus: list[tuple[str, Path, PurePosixPath]] = []
    for name in names:
        host = known.get(name)
        if host is None or not host.exists():
            raise UnknownDatasetError(
                f"Jeu de données « {name} » inconnu du projet {project_id}. "
                "Les jeux disponibles sont ceux déposés dans le projet ; un nom "
                "absent est refusé avant l'exécution.",
                dataset=name,
                project_id=project_id,
            )
        resolus.append((name, host, PurePosixPath(f"/data/{name}")))
    return resolus


def dataset_sha256(resolved: list[tuple[str, Path, PurePosixPath]]) -> dict[str, str]:
    """SHA-256 de chaque jeu monté. Sans lui, « comment cette figure a-t-elle été
    obtenue » reste sans réponse complète : un fichier modifié depuis en produirait
    une autre."""
    empreintes: dict[str, str] = {}
    for name, host, _ in resolved:
        empreintes[name] = hashlib.sha256(host.read_bytes()).hexdigest()
    return empreintes


# --- Rapprochement attendu / produit --------------------------------------


def reconcile(
    expected: list[ExpectedArtifact], produced: list[Path]
) -> tuple[list[tuple[ExpectedArtifact, Path]], list[ExpectedArtifact], list[Path]]:
    """(attendus produits, attendus absents, produits non déclarés)."""
    par_nom = {p.name: p for p in produced}
    declares = {a.filename for a in expected}
    matched = [(a, par_nom[a.filename]) for a in expected if a.filename in par_nom]
    missing = [a for a in expected if a.filename not in par_nom]
    undeclared = [p for p in produced if p.name not in declares]
    return matched, missing, undeclared


def _numbered(code: str) -> str:
    return "\n".join(f"{i:>3} | {ligne}" for i, ligne in enumerate(code.splitlines(), 1))


def format_execution_error(stderr: str, limit_exceeded: str | None, code: str) -> str:
    """Message de reprise sur erreur d'exécution.

    Un dépassement (mémoire, CPU, mur) est distingué d'une erreur de syntaxe :
    l'agent doit savoir s'il a écrit du code faux ou du code trop coûteux."""
    tail = "\n".join(stderr.splitlines()[-STDERR_TAIL_LINES:])
    if limit_exceeded:
        entete = (
            f"Le code a été interrompu : {_LIMIT_LABEL[limit_exceeded]}. Ce n'est PAS "
            "une erreur de syntaxe — allège le calcul : moins de données en mémoire, "
            "algorithme plus simple, ou résultats agrégés."
        )
    else:
        entete = "Le code a échoué à l'exécution. Corrige l'erreur d'après stderr ci-dessous."
    return f"{entete}\n\nDernières lignes de stderr :\n{tail}\n\nCode fourni :\n{_numbered(code)}"


def format_missing(missing: list[ExpectedArtifact], code: str) -> str:
    noms = ", ".join(a.filename for a in missing)
    return (
        f"Le code s'est exécuté sans erreur mais n'a pas produit les artefacts déclarés : "
        f"{noms}. Écris ces fichiers dans le répertoire courant, sous ces noms EXACTS.\n\n"
        f"Code fourni :\n{_numbered(code)}"
    )


# --- Agent et guardrail ---------------------------------------------------


def _agent_ctx(
    intent: str,
    resolved: list[tuple[str, Path, PurePosixPath]],
    correction: str | None,
) -> dict:
    return {
        "intent": intent,
        "datasets": [{"name": n, "guest_path": str(gp)} for n, _, gp in resolved],
        "correction": correction,
    }


async def _generate(agent: CodeAgent, state: GraphState, ctx: dict) -> CodeProposal:
    brut = await agent.run(state, ctx)
    resultat = await validate_output(brut, CodeProposal)
    if not resultat.ok:
        raise CodeExecutionError(resultat.error_message or "proposition de code invalide")
    return resultat.parsed  # type: ignore[return-value]


async def propose(
    conn: aiosqlite.Connection,
    project_id: int,
    manager: LLMManager,
    intent: str,
    datasets: list[str],
    known_datasets: dict[str, Path],
) -> CodeProposal:
    """Fait générer une proposition par l'agent et la valide (US-201).

    Les jeux cités sont vérifiés ici aussi : proposer un code qui lit un jeu
    inexistant ne servirait à rien."""
    resolved = resolve_datasets(project_id, datasets, known_datasets)
    agent = CodeAgent(manager)
    state = initial_state(project_id)
    return await _generate(agent, state, _agent_ctx(intent, resolved, None))


# --- Exécution ------------------------------------------------------------


async def _execution_out(conn: aiosqlite.Connection, exec_id: int) -> CodeExecutionOut:
    async with conn.execute(
        "SELECT id, origin, sandbox_level, exit_code, duration_ms, started_at"
        " FROM code_execution WHERE id = ?",
        (exec_id,),
    ) as cur:
        row = await cur.fetchone()
    return CodeExecutionOut(
        id=int(row[0]),
        origin=str(row[1]),
        sandbox_level=str(row[2]),
        exit_code=int(row[3]) if row[3] is not None else 0,
        duration_ms=int(row[4]) if row[4] is not None else 0,
        started_at=str(row[5]),
        artifacts=await artifact_service.list_for_execution(conn, exec_id),
    )


_LEVEL_OF = {"wasm": 1, "native": 2}

_RESULT_SELECT = (
    "SELECT id, exit_code, stdout, stderr, duration_ms, sandbox_level, timed_out,"
    " limit_exceeded, network_isolation_guaranteed FROM code_execution"
)


async def _result_out(conn: aiosqlite.Connection, row) -> ExecutionResultOut:
    """Ligne `code_execution` -> `ExecutionResult` du contrat.

    Les garanties sont RELUES, jamais réinférées du niveau : une exécution
    native sous Linux peut avoir obtenu ou non son namespace réseau.
    """
    exec_id = int(row[0])
    artefacts = await artifact_service.list_for_execution(conn, exec_id)
    return ExecutionResultOut(
        id=exec_id,
        exit_code=int(row[1]) if row[1] is not None else 0,
        stdout=str(row[2] or ""),
        stderr=str(row[3] or ""),
        duration_ms=int(row[4]) if row[4] is not None else 0,
        level=_LEVEL_OF[str(row[5])],  # type: ignore[arg-type]
        timed_out=bool(row[6]),
        limit_exceeded=row[7],
        network_isolation_guaranteed=bool(row[8]),
        artifacts=[a.rel_path for a in artefacts],
    )


async def list_executions(conn: aiosqlite.Connection, project_id: int) -> list[ExecutionResultOut]:
    """Historique, au format `ExecutionResult` du contrat (US-004)."""
    async with conn.execute(
        f"{_RESULT_SELECT} WHERE project_id = ? ORDER BY id DESC", (project_id,)
    ) as cur:
        lignes = await cur.fetchall()
    return [await _result_out(conn, r) for r in lignes]


async def execute_direct(
    conn: aiosqlite.Connection,
    project_id: int,
    origin: SandboxOrigin,
    mode: SandboxMode,
    code: str,
    datasets: list[str],
    limits: ResourceLimits,
    known_datasets: dict[str, Path],
) -> ExecutionResultOut:
    """Exécution DIRECTE d'un code fourni (US-004), telle que décrite au contrat.

    Distincte du pipeline d'US-401 : aucun agent, aucun rapprochement
    d'artefacts, aucune boucle de correction. Le niveau demandé est respecté
    pour une origine utilisateur — sous consentement au niveau natif — et
    ramené au niveau 1 pour une origine agent, l'abaissement étant rapporté.
    """
    resolved = resolve_datasets(project_id, datasets, known_datasets)
    mounts = [MountSpec(host_path=h, guest_path=g, writable=False) for _, h, g in resolved]
    output_dir = _artifacts_root(project_id) / f"exec-{_stamp()}"

    result, exec_id = await factory.run_sandboxed(
        conn, project_id, origin, mode, code, mounts, limits, output_dir
    )
    racine = get_settings().data_dir
    return ExecutionResultOut(
        id=exec_id,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        level=int(result.level),  # type: ignore[arg-type]
        timed_out=result.timed_out,
        limit_exceeded=result.limit_exceeded,
        network_isolation_guaranteed=result.network_isolation_guaranteed,
        # Un agent qui demande le natif est ramené au niveau 1 : on le DIT.
        downgraded_from_requested_mode=(
            origin == SandboxOrigin.AGENT and mode == SandboxMode.NATIVE
        ),
        artifacts=[_relative(p, racine) for p in result.artifacts],
    )


def _relative(chemin: Path, racine: Path) -> str:
    try:
        return chemin.relative_to(racine).as_posix()
    except ValueError:  # hors du répertoire de données : on rend le nom seul
        return chemin.name


async def execute(
    conn: aiosqlite.Connection,
    project_id: int,
    manager: LLMManager,
    proposal: CodeProposal,
    known_datasets: dict[str, Path],
    task_id: int | None = None,
) -> CodeExecutionOut:
    """Exécute une proposition au niveau 1, corrige en boucle, rapproche, persiste.

    Retour : l'exécution qui a produit les artefacts attendus. En cas d'échec
    répété, `CodeExecutionError` (ERROR_STATE) — jamais une figure qu'on sait
    absente ou fausse.
    """
    resolved = resolve_datasets(project_id, proposal.datasets, known_datasets)
    mounts = [MountSpec(host_path=h, guest_path=g, writable=False) for _, h, g in resolved]
    sha = dataset_sha256(resolved)
    racine = get_settings().data_dir
    base = _artifacts_root(project_id)
    agent = CodeAgent(manager)
    state = initial_state(project_id)
    courant = proposal

    for essai in range(1, MAX_CODE_CORRECTIONS + 1):
        output_dir = base / f"exec-{_stamp()}-{essai}"
        executor = factory.select(SandboxOrigin.AGENT, SandboxMode.WASM)
        # PackageUnavailableInWasmError s'échappe volontairement : elle NE relance
        # PAS l'agent (US-401) — c'est une décision humaine (niveau 2).
        result = await executor.run(courant.code, mounts, ResourceLimits(), output_dir)
        exec_id = await factory.persist_execution(
            conn, project_id, SandboxOrigin.AGENT, result, courant.code
        )

        if result.exit_code != 0:
            if essai >= MAX_CODE_CORRECTIONS:
                await _stop(conn, task_id)
                raise CodeExecutionError(
                    f"{MAX_CODE_CORRECTIONS} essais d'exécution rejetés. Dernier motif : "
                    f"{(result.stderr or '').splitlines()[-1:] or ['(aucun)']}"
                )
            courant = await _generate(
                agent,
                state,
                _agent_ctx(
                    proposal.intent,
                    resolved,
                    format_execution_error(result.stderr, result.limit_exceeded, courant.code),
                ),
            )
            continue

        matched, missing, undeclared = reconcile(courant.expected_artifacts, result.artifacts)
        if missing:
            if essai >= MAX_CODE_CORRECTIONS:
                await _stop(conn, task_id)
                raise CodeExecutionError(
                    f"Artefacts attendus jamais produits : {[a.filename for a in missing]}."
                )
            courant = await _generate(
                agent,
                state,
                _agent_ctx(proposal.intent, resolved, format_missing(missing, courant.code)),
            )
            continue

        matched_rel = [(a, p.relative_to(racine).as_posix()) for a, p in matched]
        undeclared_rel = [(p.name, p.relative_to(racine).as_posix()) for p in undeclared]
        await artifact_service.save_artifacts(
            conn,
            exec_id,
            matched_rel,
            undeclared_rel,
            courant.code,
            courant.random_seed,
            library_versions={},
            dataset_sha256=sha,
            duration_ms=result.duration_ms,
        )
        if undeclared_rel:
            logger.info(
                "Exécution %s : %s artefact(s) inattendu(s) conservé(s), non rattaché(s)",
                exec_id,
                len(undeclared_rel),
            )
        return await _execution_out(conn, exec_id)

    raise CodeExecutionError("Boucle de correction épuisée.")  # pragma: no cover - garde-fou


async def _stop(conn: aiosqlite.Connection, task_id: int | None) -> None:
    if task_id is not None:
        await task_service.update(conn, task_id, state=WorkflowState.ERROR_STATE)
