-- Migration 003 — nature de la section d'un chunk (US-102).
--
-- La bibliographie d'un article est du texte comme un autre : elle
-- s'indexerait sans rien signaler. La rendre au rédacteur lui donnerait une
-- liste de titres d'articles qu'il n'a pas lus — la matière première exacte
-- d'une citation inventée, que les guardrails de §5.5 sont censés rendre
-- impossible.
--
-- Le marqueur permet de l'indexer quand même, pour l'extraction
-- bibliographique, tout en l'écartant des résultats destinés à la rédaction.

ALTER TABLE chunk ADD COLUMN section_kind TEXT NOT NULL DEFAULT 'body';

CREATE INDEX idx_chunk_section ON chunk(source_id, section_kind);
