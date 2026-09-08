-- Migration 004 — une section rédigée survit à la suppression de son nœud.
--
-- `draft_section.plan_node_id` était en ON DELETE CASCADE (US-002, écrit
-- avant que la notion de section orpheline existe). Conséquence : marquer
-- une section ORPHANED puis supprimer le nœud la supprimait quand même. Le
-- marquage ne servait à rien, et US-PLAN-001 interdit explicitement de
-- supprimer silencieusement une section rédigée — un chapitre représente
-- des jours de travail.
--
-- La colonne devient nullable et passe en ON DELETE SET NULL : une section
-- orpheline n'a plus de nœud, ce qui est exactement sa définition. SQLite
-- ne sait pas modifier une clé étrangère : la table est reconstruite.

CREATE TABLE draft_section_nouveau (
  id            INTEGER PRIMARY KEY,
  plan_node_id  INTEGER REFERENCES plan_node(id) ON DELETE SET NULL,
  content_qmd   TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL,
  quality_score REAL,
  version       INTEGER NOT NULL DEFAULT 1,
  generated_at  TEXT,
  validated_at  TEXT
);

INSERT INTO draft_section_nouveau
  (id, plan_node_id, content_qmd, status, quality_score, version, generated_at, validated_at)
SELECT id, plan_node_id, content_qmd, status, quality_score, version, generated_at, validated_at
FROM draft_section;

DROP TABLE draft_section;
ALTER TABLE draft_section_nouveau RENAME TO draft_section;

CREATE INDEX idx_draft_node ON draft_section(plan_node_id);
CREATE INDEX idx_draft_status ON draft_section(status);
