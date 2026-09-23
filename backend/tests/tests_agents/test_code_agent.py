"""US-401 — modèle de proposition de code et agent code.

Les validateurs sont éprouvés sans exécuter la moindre ligne : un label MyST, un
nom de fichier avec chemin, une graine manquante sont refusés au modèle, avant
tout runtime. L'agent, lui, est exercé contre un backend factice.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import PackageUnavailableInWasmError
from app.llm.manager import LLMManager
from app.models.code import CodeProposal, ExpectedArtifact
from app.sandbox.base import ExecutionResult, SandboxLevel, collect_artifacts
from app.services import code_service
from tests.tests_agents.test_writer_agent import FakeBackend


def _figure(label: str = "fig-filtration", filename: str = "filtration.png") -> dict:
    return {"filename": filename, "kind": "figure", "caption": "Filtration", "label": label}


# --- Renvois Quarto -------------------------------------------------------


def test_label_must_be_quarto_syntax() -> None:
    ExpectedArtifact.model_validate(_figure(label="fig-filtration-renale"))
    ExpectedArtifact.model_validate({**_figure(), "kind": "table", "label": "tbl-cohortes"})
    ExpectedArtifact.model_validate({**_figure(), "kind": "data", "label": "jeu-brut"})


@pytest.mark.parametrize(
    "kind,label",
    [
        ("figure", "tbl-mauvais-prefixe"),  # préfixe du mauvais type
        ("table", "fig-mauvais-prefixe"),
        ("figure", "fig-Filtration"),  # majuscules
        ("figure", "fig_underscore"),  # underscore, pas trait d'union
        ("figure", "sans-prefixe"),
    ],
)
def test_label_wrong_prefix_or_shape_rejected(kind: str, label: str) -> None:
    with pytest.raises(ValidationError):
        ExpectedArtifact.model_validate({**_figure(), "kind": kind, "label": label})


def test_myst_label_rejected() -> None:
    """MyST écrit « fig:foo » (deux points) ; Quarto « fig-foo » (ADR-006)."""
    with pytest.raises(ValidationError, match="MyST"):
        ExpectedArtifact.model_validate(_figure(label="fig:filtration"))


# --- Nom de fichier -------------------------------------------------------


@pytest.mark.parametrize("filename", ["sous/dossier.png", "a\\b.png", "../evasion.png", ".."])
def test_filename_rejects_path_separator(filename: str) -> None:
    with pytest.raises(ValidationError):
        ExpectedArtifact.model_validate(_figure(filename=filename))


# --- Graine et reproductibilité -------------------------------------------


def _proposal(code: str, seed: int | None = None) -> CodeProposal:
    return CodeProposal.model_validate(
        {
            "code": code,
            "intent": "Tracer la filtration",
            "expected_artifacts": [_figure()],
            "random_seed": seed,
        }
    )


def test_seed_required_when_numpy_imported() -> None:
    with pytest.raises(ValidationError, match="random_seed"):
        _proposal("import numpy as np\nnp.random.rand(3)\n")
    # Avec graine : accepté.
    assert _proposal("import numpy as np\n", seed=1234).random_seed == 1234


def test_seed_required_when_random_imported() -> None:
    with pytest.raises(ValidationError, match="random_seed"):
        _proposal("import random\nrandom.random()\n")


def test_seed_not_required_without_stochastic_imports() -> None:
    """Un code déterministe (pas de numpy ni random) n'exige pas de graine."""
    assert _proposal("import pandas as pd\nprint(pd.__version__)\n").random_seed is None


# --- Messages de reprise (fonctions pures) --------------------------------


def test_stderr_truncated_to_last_20_lines() -> None:
    stderr = "\n".join(f"ligne {i}" for i in range(1, 31))
    message = code_service.format_execution_error(stderr, None, "print('x')")
    assert "ligne 30" in message
    assert "ligne 11" in message
    assert "ligne 10" not in message  # au-delà des 20 dernières


def test_limit_exceeded_message_differs_from_syntax_error() -> None:
    """L'agent doit savoir s'il a écrit du code faux ou du code trop coûteux."""
    syntaxe = code_service.format_execution_error("SyntaxError: bad", None, "x =")
    memoire = code_service.format_execution_error("Killed", "memory", "x = [0]*10**12")
    assert "syntaxe" not in memoire.split("\n")[0] or "PAS une erreur de syntaxe" in memoire
    assert "mémoire" in memoire
    assert memoire != syntaxe


# --- Exécution : niveau, rapprochement, boucle (sandbox factice) ----------


def _proposal_obj(**kw) -> CodeProposal:
    base = {"code": "print('ok')", "intent": "tracer", "expected_artifacts": [_figure()]}
    base.update(kw)
    return CodeProposal.model_validate(base)


def _proposal_reply(**kw) -> str:
    return json.dumps(
        {"code": "print('ok')", "intent": "tracer", "expected_artifacts": [_figure()], **kw},
        ensure_ascii=False,
    )


class FakeSandbox:
    """Exécuteur factice de niveau 1. Chaque « script » décrit un essai :
    fichiers produits, code de sortie, dépassement, ou levée de paquet absent."""

    level = SandboxLevel.WASM

    def __init__(self, scripts: list[dict]) -> None:
        self.scripts = list(scripts)
        self.calls: list[str] = []

    async def run(self, code, mounts, limits, output_dir):
        self.calls.append(code)
        beh = self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]
        if beh.get("raise"):
            raise PackageUnavailableInWasmError.suggest_level2("torch")
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in beh.get("files", []):
            (output_dir / name).write_text("x", encoding="utf-8")
        return ExecutionResult(
            exit_code=beh.get("exit", 0),
            stdout=beh.get("stdout", ""),
            stderr=beh.get("stderr", ""),
            duration_ms=5,
            level=SandboxLevel.WASM,
            timed_out=beh.get("timed_out", False),
            limit_exceeded=beh.get("limit"),
            artifacts=collect_artifacts(output_dir),
            network_isolation_guaranteed=True,
        )


def _wire(monkeypatch, tmp_path, sandbox: FakeSandbox) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    monkeypatch.setattr(code_service.factory, "select", lambda *a, **k: sandbox)


async def test_unknown_dataset_rejected_before_execution(projet, monkeypatch, tmp_path) -> None:
    _, conn = projet
    sandbox = FakeSandbox([{"files": ["filtration.png"]}])
    _wire(monkeypatch, tmp_path, sandbox)
    proposal = _proposal_obj(datasets=["inconnu"])

    with pytest.raises(code_service.UnknownDatasetError):
        await code_service.execute(conn, 1, LLMManager(FakeBackend()), proposal, {})
    assert sandbox.calls == [], "aucune exécution avant la résolution des jeux"


async def test_agent_origin_always_level1(projet, monkeypatch, tmp_path) -> None:
    """Le code d'agent passe au niveau 1, quel que soit le contexte."""
    _, conn = projet
    captures: list = []
    real_select = code_service.factory.select

    def spy(origin, mode, platform=None):
        captures.append((origin, mode))
        return FakeSandbox([{"files": ["filtration.png"]}])

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    monkeypatch.setattr(code_service.factory, "select", spy)
    await code_service.execute(conn, 1, LLMManager(FakeBackend()), _proposal_obj(), {})

    assert captures, "la fabrique doit être sollicitée"
    origin, _mode = captures[0]
    assert origin.value == "agent"
    assert real_select(origin, _mode, platform="win32").level == SandboxLevel.WASM


async def test_missing_expected_artifact_reruns_agent(projet, monkeypatch, tmp_path) -> None:
    _, conn = projet
    # 1er essai : rien produit -> relance ; 2e : la figure est là.
    sandbox = FakeSandbox([{"files": []}, {"files": ["filtration.png"]}])
    backend = FakeBackend([_proposal_reply()])  # proposition régénérée
    _wire(monkeypatch, tmp_path, sandbox)

    out = await code_service.execute(conn, 1, LLMManager(backend), _proposal_obj(), {})

    assert len(sandbox.calls) == 2
    assert len(backend.users) == 1  # l'agent a été relancé une fois
    assert [a.filename for a in out.artifacts] == ["filtration.png"]
    assert out.artifacts[0].declared is True


async def test_undeclared_artifact_kept_but_flagged_and_not_attached(
    projet, monkeypatch, tmp_path
) -> None:
    _, conn = projet
    sandbox = FakeSandbox([{"files": ["filtration.png", "extra.csv"]}])
    _wire(monkeypatch, tmp_path, sandbox)

    out = await code_service.execute(conn, 1, LLMManager(FakeBackend()), _proposal_obj(), {})

    par_nom = {a.filename: a for a in out.artifacts}
    assert par_nom["filtration.png"].declared is True
    assert par_nom["extra.csv"].declared is False, "l'inattendu est conservé, signalé"
    assert par_nom["extra.csv"].draft_section_id is None, "jamais rattaché seul"


async def test_package_unavailable_does_not_rerun_agent(projet, monkeypatch, tmp_path) -> None:
    _, conn = projet
    sandbox = FakeSandbox([{"raise": True}])
    backend = FakeBackend([_proposal_reply()])
    _wire(monkeypatch, tmp_path, sandbox)

    with pytest.raises(PackageUnavailableInWasmError):
        await code_service.execute(conn, 1, LLMManager(backend), _proposal_obj(), {})

    assert len(sandbox.calls) == 1, "une seule tentative"
    assert backend.users == [], "l'agent n'est PAS relancé sur un paquet absent"


async def test_correction_loop_capped_at_three(projet, monkeypatch, tmp_path) -> None:
    _, conn = projet
    sandbox = FakeSandbox([{"exit": 1, "stderr": "boom"}])  # échoue toujours
    backend = FakeBackend([_proposal_reply(), _proposal_reply()])
    _wire(monkeypatch, tmp_path, sandbox)

    with pytest.raises(code_service.CodeExecutionError):
        await code_service.execute(conn, 1, LLMManager(backend), _proposal_obj(), {})

    assert len(sandbox.calls) == code_service.MAX_CODE_CORRECTIONS == 3
    assert len(backend.users) == 2  # deux relances, puis arrêt
