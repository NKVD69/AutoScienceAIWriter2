"""US-003 — stabilite octet pour octet des prompts systeme. ADR-003."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.llm.prompts import registry
from app.llm.prompts.registry import AgentName, all_agents, get_system_prompt, prompt_version


def test_every_agent_has_a_prompt() -> None:
    assert set(all_agents()) == set(AgentName)
    for agent in AgentName:
        assert get_system_prompt(agent).strip()


@pytest.mark.parametrize("agent", list(AgentName))
def test_system_prompt_byte_stable_across_calls(agent: AgentName) -> None:
    """Le prefix caching n'opere que sur un prefixe strictement identique."""
    premier = get_system_prompt(agent)
    for _ in range(5):
        assert get_system_prompt(agent).encode("utf-8") == premier.encode("utf-8")


@pytest.mark.parametrize("agent", list(AgentName))
def test_system_prompt_contains_no_interpolation(agent: AgentName) -> None:
    """Aucun marqueur de formatage : une donnee variable annulerait le cache."""
    prompt = get_system_prompt(agent)
    assert "{" not in prompt
    assert "}" not in prompt
    assert "%s" not in prompt


def test_prompts_are_module_level_constants() -> None:
    """Les prompts sont des constantes, jamais construites a l'execution.

    Le controle porte sur la source : une f-string ou une concatenation avec
    un appel de fonction produirait un prefixe variable sans qu'aucun test de
    valeur ne le remarque, tant que la valeur observee reste la meme.
    """
    source = Path(registry.__file__).read_text(encoding="utf-8")
    arbre = ast.parse(source)
    noms_prompts = {
        target.id
        for noeud in arbre.body
        if isinstance(noeud, ast.Assign)
        for target in noeud.targets
        if isinstance(target, ast.Name) and target.id.endswith("_PROMPT")
    }
    assert len(noms_prompts) == len(AgentName)

    for noeud in arbre.body:
        if not isinstance(noeud, ast.Assign):
            continue
        cibles = {t.id for t in noeud.targets if isinstance(t, ast.Name)}
        if not cibles & noms_prompts:
            continue
        for interne in ast.walk(noeud.value):
            assert not isinstance(interne, ast.JoinedStr), f"{cibles} est une f-string"
            assert not isinstance(interne, ast.Call), f"{cibles} appelle une fonction"


@pytest.mark.parametrize("agent", list(AgentName))
def test_prompt_version_is_stable_and_short(agent: AgentName) -> None:
    version = prompt_version(agent)
    assert len(version) == 12
    assert version == prompt_version(agent)


def test_prompt_version_changes_when_prompt_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    avant = prompt_version(AgentName.WRITER)
    modifie = dict(registry._PROMPTS)
    modifie[AgentName.WRITER] = registry.WRITER_PROMPT + "Une phrase de plus.\n"
    monkeypatch.setattr(registry, "_PROMPTS", modifie)
    assert prompt_version(AgentName.WRITER) != avant


def test_agent_versions_are_distinct() -> None:
    versions = {prompt_version(a) for a in AgentName}
    assert len(versions) == len(AgentName)
