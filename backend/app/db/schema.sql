-- Schéma canonique du fichier projet — US-002, ADR-001, ADR-002.
--
-- INVARIANT CENTRAL : vec_chunk.rowid == chunk.id
--
-- SQLite n'applique PAS les contraintes de clé étrangère déclarées sur une
-- table virtuelle. `vec0` est une table virtuelle : une clause REFERENCES y
-- serait acceptée à la création puis ignorée à l'exécution. L'intégrité
-- référentielle vit donc dans la table relationnelle `chunk`, jamais dans
-- `vec_chunk`, et la cascade des vecteurs passe par un TRIGGER parce que
-- ON DELETE CASCADE ne traverse pas une table virtuelle.
--
-- Conventions : clés primaires INTEGER PRIMARY KEY · horodatages TEXT
-- ISO-8601 UTC · booléens INTEGER 0/1 · toute colonne REFERENCES porte une
-- clause ON DELETE explicite · toute colonne JSON est suffixée _json.
--
-- La dimension d'embedding est substituée à l'application depuis
-- settings.embedding_dim : le littéral {embedding_dim} ci-dessous est le seul
-- endroit où elle apparaît.

CREATE TABLE project (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  subject        TEXT NOT NULL,
  discipline     TEXT,
  language       TEXT NOT NULL DEFAULT 'fr',
  academic_level TEXT NOT NULL CHECK (academic_level IN ('master','ingenieur','doctorat','hdr')),
  target_words   INTEGER,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE source_document (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  title       TEXT NOT NULL,
  authors     TEXT,
  year        INTEGER,
  doi         TEXT,
  url         TEXT,
  venue       TEXT,
  is_preprint INTEGER NOT NULL DEFAULT 0 CHECK (is_preprint IN (0,1)),
  file_path   TEXT,
  sha256      TEXT,
  imported_at TEXT NOT NULL,
  approved_at TEXT,
  -- Cle publiee dans le .bib (US-501, migration 005). Attribuee au
  -- premier export puis relue telle quelle : une cle qui change casse
  -- les renvois d'un document deja relu.
  bibtex_key  TEXT
);

CREATE TABLE chunk (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  ordinal     INTEGER NOT NULL,
  text        TEXT NOT NULL,
  page_start  INTEGER,
  page_end    INTEGER,
  token_count INTEGER,
  -- 'body' ou 'references' (US-102, migration 003). Un chunk de
  -- bibliographie est indexé pour l'extraction de références, jamais rendu
  -- à la rédaction : ce serait la matière première d'une citation inventée.
  section_kind TEXT NOT NULL DEFAULT 'body',
  UNIQUE(source_id, ordinal)
);

CREATE TABLE plan (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  problematique TEXT NOT NULL,
  status        TEXT NOT NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL
);

CREATE TABLE plan_node (
  id           INTEGER PRIMARY KEY,
  plan_id      INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
  parent_id    INTEGER REFERENCES plan_node(id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  level        INTEGER NOT NULL,
  title        TEXT NOT NULL,
  objective    TEXT,
  target_words INTEGER
);

-- `plan_node_id` est NULLABLE et en SET NULL (migration 004) : une section
-- rédigée survit à la suppression de son nœud, marquée ORPHANED. La
-- supprimer avec le nœud détruirait des jours de travail sans le dire.
CREATE TABLE draft_section (
  id            INTEGER PRIMARY KEY,
  plan_node_id  INTEGER REFERENCES plan_node(id) ON DELETE SET NULL,
  content_qmd   TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL,
  quality_score REAL,
  version       INTEGER NOT NULL DEFAULT 1,
  generated_at  TEXT,
  validated_at  TEXT,
  -- US-302 : comptes persistés à la rédaction, pour mesurer sourçage et
  -- complétude en Python plutôt que de les faire estimer par le modèle.
  claim_count         INTEGER NOT NULL DEFAULT 0,
  sourced_claim_count INTEGER NOT NULL DEFAULT 0
);

-- citation.source_id est en RESTRICT délibérément (spécifications §4.2) :
-- on n'autorise pas la suppression d'une source encore citée dans une section.
-- chunk_id passe à NULL si le chunk disparaît — la citation survit à une
-- réindexation, seule sa localisation fine est perdue.
CREATE TABLE citation (
  id               INTEGER PRIMARY KEY,
  draft_section_id INTEGER NOT NULL REFERENCES draft_section(id) ON DELETE CASCADE,
  source_id        INTEGER NOT NULL REFERENCES source_document(id) ON DELETE RESTRICT,
  chunk_id         INTEGER REFERENCES chunk(id) ON DELETE SET NULL,
  bibtex_key       TEXT NOT NULL,
  locator          TEXT,
  verified         INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1))
);

-- Relecture et score de qualité (US-302). Un rapport par version de section,
-- conservé pour la comparaison ; jamais écrasé.
CREATE TABLE review_report (
  id               INTEGER PRIMARY KEY,
  draft_section_id INTEGER NOT NULL REFERENCES draft_section(id) ON DELETE CASCADE,
  overall_score    REAL NOT NULL,
  verdict          TEXT NOT NULL,
  scores_json      TEXT NOT NULL,
  findings_json    TEXT NOT NULL,
  auto_correct     INTEGER NOT NULL DEFAULT 0 CHECK (auto_correct IN (0,1)),
  created_at       TEXT NOT NULL
);

CREATE TABLE code_execution (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  origin        TEXT NOT NULL CHECK (origin IN ('agent','user')),
  sandbox_level TEXT NOT NULL CHECK (sandbox_level IN ('wasm','native')),
  code          TEXT NOT NULL,
  stdout        TEXT,
  stderr        TEXT,
  exit_code     INTEGER,
  duration_ms   INTEGER,
  started_at    TEXT NOT NULL
);

CREATE TABLE task (
  id           INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  state        TEXT NOT NULL,
  agent        TEXT,
  payload_json TEXT,
  retry_count  INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

-- audit_log.project_id ne déclare volontairement aucune clé étrangère
-- (spécifications §4.2 : « sans cascade »). Un journal à détection
-- d'altération qui disparaîtrait avec son projet ne prouverait plus rien
-- sur ce qui a été fait avant la suppression.
CREATE TABLE audit_log (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  prev_hash  TEXT,
  hash       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE consent (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  scope      TEXT NOT NULL,
  granted    INTEGER NOT NULL CHECK (granted IN (0,1)),
  granted_at TEXT NOT NULL,
  details    TEXT,
  UNIQUE(project_id, scope)
);

CREATE TABLE model_config (
  id             INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  llm_model      TEXT NOT NULL,
  llm_quant      TEXT,
  embedding_model TEXT NOT NULL,
  embedding_dim  INTEGER NOT NULL,
  created_at     TEXT NOT NULL,
  UNIQUE(project_id)
);

-- Table virtuelle : la colonne embedding et rien d'autre. Aucune clause
-- REFERENCES ici — elle serait ignorée en silence.
CREATE VIRTUAL TABLE vec_chunk USING vec0(
  embedding float[{embedding_dim}]
);

CREATE TRIGGER chunk_after_delete AFTER DELETE ON chunk
BEGIN
  DELETE FROM vec_chunk WHERE rowid = old.id;
END;

CREATE INDEX idx_source_project   ON source_document(project_id);
CREATE INDEX idx_source_doi       ON source_document(doi);
CREATE UNIQUE INDEX idx_source_bibtex_key ON source_document(bibtex_key)
  WHERE bibtex_key IS NOT NULL;
CREATE INDEX idx_chunk_source     ON chunk(source_id);
CREATE INDEX idx_plan_node_plan   ON plan_node(plan_id, ordinal);
CREATE INDEX idx_draft_node       ON draft_section(plan_node_id);
CREATE INDEX idx_citation_section ON citation(draft_section_id);
CREATE INDEX idx_task_state       ON task(project_id, state);
CREATE INDEX idx_audit_project    ON audit_log(project_id, id);

-- Unicite du chainage d'audit (US-701, migration 002). Deux entrees
-- partageant le meme prev_hash dans un projet sont une fourche : la
-- contrainte la transforme en violation a l'ecriture plutot qu'en
-- incoherence decouverte des mois plus tard.
CREATE UNIQUE INDEX idx_audit_prev ON audit_log(project_id, prev_hash);
CREATE INDEX idx_chunk_section ON chunk(source_id, section_kind);
CREATE INDEX idx_draft_status ON draft_section(status);
CREATE INDEX idx_review_section ON review_report(draft_section_id, id);
