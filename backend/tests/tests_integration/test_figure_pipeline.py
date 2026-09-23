"""US-401 — chaîne complète de production de figure (intégration).

Dataset CSV → proposition de code → exécution au niveau 1 → figure produite →
rattachement → renvoi présent dans le .qmd.

**Ce test saute tant que la pile scientifique n'est pas vendorisée hors-ligne.**
Le niveau 1 ne charge que Pyodide « core » (US-004) ; numpy et matplotlib
viennent de roues qui, sous ADR-010, doivent être vendorisées dans la
distribution locale. Deux prérequis pour l'activer, notés en réserve d'US-401 :
les roues vendorisées, et le chargement des paquets importés par le harnais
Wasm (raffinement d'US-004). Le corps du test est prêt pour cet état.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

import pytest

from app.core.config import get_settings
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.llm.manager import LLMManager
from app.models.code import CodeProposal, ExpectedArtifact
from app.sandbox.wasm import default_index_url
from app.services import artifact_service, code_service
from tests.tests_agents.test_writer_agent import FakeBackend

NOW = datetime.now(UTC).isoformat()


def _scientific_stack_offline() -> bool:
    dist = default_index_url()
    if not dist.exists():
        return False
    return any(dist.glob("numpy*.whl")) and any(dist.glob("matplotlib*.whl"))


needs_wheels = pytest.mark.skipif(
    shutil.which("node") is None or not _scientific_stack_offline(),
    reason="pile scientifique Pyodide (numpy/matplotlib) non vendorisée hors-ligne (US-401)",
)

FIGURE_CODE = (
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "import csv\n"
    "xs, ys = [], []\n"
    "with open('/data/donnees') as f:\n"
    "    for row in csv.DictReader(f):\n"
    "        xs.append(float(row['x']))\n"
    "        ys.append(float(row['y']))\n"
    "plt.figure()\n"
    "plt.plot(xs, ys)\n"
    "plt.savefig('courbe.png')\n"
)


@pytest.mark.integration
@needs_wheels
async def test_figure_pipeline_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    donnees = tmp_path / "donnees.csv"
    donnees.write_text("x,y\n1,2\n2,4\n3,6\n", encoding="utf-8")

    async with connect(tmp_path / "projet.sqlite") as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1, 'P', 'S', 'fr', 'doctorat', ?, ?)",
                (NOW, NOW),
            )
            cur = await conn.execute(
                "INSERT INTO draft_section (plan_node_id, content_qmd, status, version,"
                " generated_at) VALUES (NULL, 'Résultats.', 'REVIEWING', 1, ?)",
                (NOW,),
            )
            section_id = int(cur.lastrowid or 0)

        proposal = CodeProposal(
            code=FIGURE_CODE,
            intent="Tracer y en fonction de x",
            datasets=["donnees"],
            expected_artifacts=[
                ExpectedArtifact(
                    filename="courbe.png", kind="figure", caption="Courbe", label="fig-courbe"
                )
            ],
            random_seed=0,
        )

        outcome = await code_service.execute(
            conn, 1, LLMManager(FakeBackend()), proposal, {"donnees": donnees}
        )

        assert outcome.exit_code == 0
        assert [a.filename for a in outcome.artifacts] == ["courbe.png"]
        assert outcome.artifacts[0].dataset_sha256  # SHA-256 du jeu conservé

        section = await artifact_service.attach(conn, 1, section_id, outcome.artifacts[0].id)
        assert "{#fig-courbe}" in section.content_qmd
