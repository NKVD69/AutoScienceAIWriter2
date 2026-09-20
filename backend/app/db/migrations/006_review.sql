-- Migration 006 — relecture et score de qualité (US-302).
--
-- Deux besoins que la rédaction (US-301) ne couvrait pas.
--
-- 1. Le score de complétude et de sourçage se mesure pour moitié en Python,
--    pas d'après le jugement du modèle : « une requête SQL ne se trompe pas ».
--    Or les affirmations d'une section n'étaient pas persistées — seules les
--    citations l'étaient, et les affirmations non sourcées ne laissaient
--    aucune trace. On persiste donc les deux comptes au moment de la
--    rédaction : total, et sourcées. Leur rapport donne la proportion
--    d'affirmations rattachées à une source, sans réinterroger le modèle.
--
-- 2. Chaque relecture produit un rapport, et l'utilisateur doit pouvoir
--    comparer les versions. Le rapport est donc conservé par version de
--    section, jamais écrasé — comme l'est déjà la section elle-même.

ALTER TABLE draft_section ADD COLUMN claim_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE draft_section ADD COLUMN sourced_claim_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE review_report (
  id               INTEGER PRIMARY KEY,
  draft_section_id INTEGER NOT NULL REFERENCES draft_section(id) ON DELETE CASCADE,
  overall_score    REAL NOT NULL,
  verdict          TEXT NOT NULL,
  -- Scores par catégorie et findings, tels que consolidés côté serveur.
  -- Stockés en JSON : ce sont des données de rapport, jamais requêtées champ
  -- par champ, et leur forme appartient au modèle Pydantic qui les relit.
  scores_json      TEXT NOT NULL,
  findings_json    TEXT NOT NULL,
  auto_correct     INTEGER NOT NULL DEFAULT 0 CHECK (auto_correct IN (0,1)),
  created_at       TEXT NOT NULL
);

-- Le dernier rapport d'une section, et l'historique, se lisent par section.
CREATE INDEX idx_review_section ON review_report(draft_section_id, id);
