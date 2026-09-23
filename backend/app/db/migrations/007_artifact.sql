-- Migration 007 — artefacts d'exécution et leur reproductibilité (US-401).
--
-- Un code d'agent (US-401) produit des figures et des tableaux. Trois besoins
-- que `code_execution` (US-004) ne couvre pas :
--
-- 1. REPRODUCTIBILITÉ. Répondre en soutenance à « comment cette figure a-t-elle
--    été obtenue » exige le code exact, la graine, les versions des
--    bibliothèques relevées dans la sandbox et le SHA-256 des jeux de données.
--    Sans le SHA-256, un fichier modifié depuis produirait une autre figure et
--    la réponse serait fausse. Ces éléments vivent avec l'artefact, pas ailleurs.
--
-- 2. RAPPROCHEMENT. Un artefact peut être ATTENDU (déclaré par la proposition)
--    ou INATTENDU (produit mais non déclaré). Le second est conservé et signalé,
--    jamais rattaché seul à une section. Le drapeau `declared` porte la distinction.
--
-- 3. RATTACHEMENT. Une figure ou un tableau se rattache à une section : le
--    renvoi Quarto est alors inséré dans son contenu. `draft_section_id` est
--    NULLABLE (non rattaché au départ) et en SET NULL — un artefact survit à la
--    suppression de son nœud, comme la section elle-même (migration 004).

CREATE TABLE artifact (
  id                    INTEGER PRIMARY KEY,
  code_execution_id     INTEGER NOT NULL REFERENCES code_execution(id) ON DELETE CASCADE,
  draft_section_id      INTEGER REFERENCES draft_section(id) ON DELETE SET NULL,
  kind                  TEXT NOT NULL CHECK (kind IN ('figure','table','data')),
  filename              TEXT NOT NULL,
  label                 TEXT,
  caption               TEXT,
  -- Chemin relatif au répertoire du projet : un chemin absolu casserait au
  -- déplacement du projet, qu'ADR-001 autorise (l'utilisateur possède ses fichiers).
  rel_path              TEXT NOT NULL,
  -- Attendu (1) ou inattendu (0). Un inattendu n'est jamais rattaché seul.
  declared              INTEGER NOT NULL DEFAULT 1 CHECK (declared IN (0,1)),
  -- Trace de reproduction. JSON pour les paires nom -> version et nom -> sha256 :
  -- des données de rapport, relues par un modèle Pydantic, jamais requêtées champ
  -- par champ.
  code                  TEXT NOT NULL,
  random_seed           INTEGER,
  library_versions_json TEXT,
  dataset_sha256_json   TEXT,
  duration_ms           INTEGER,
  created_at            TEXT NOT NULL
);

CREATE INDEX idx_artifact_execution ON artifact(code_execution_id);
CREATE INDEX idx_artifact_section ON artifact(draft_section_id);
