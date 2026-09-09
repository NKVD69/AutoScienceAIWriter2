-- Migration 005 — clé BibTeX persistée par source (US-501, ADR-007).
--
-- La clé publiée d'une source doit être STABLE d'un export à l'autre. Une
-- clé recalculée à chaque compilation changerait dès qu'une source homonyme
-- est importée — la résolution des collisions dépend de l'ensemble des
-- sources, pas de la source seule — et casserait les renvois d'un document
-- déjà relu et annoté par un directeur de recherche.
--
-- Elle est donc attribuée une fois, écrite ici, et relue telle quelle aux
-- exports suivants. `UNIQUE` porte la garantie qui compte : deux sources ne
-- peuvent pas partager une clé, sans quoi la bibliographie rendue
-- fusionnerait deux références distinctes en une seule entrée.
--
-- NULL tant qu'aucun export n'a eu lieu : la contrainte UNIQUE de SQLite
-- laisse passer plusieurs NULL, ce qui est exactement le comportement voulu.

ALTER TABLE source_document ADD COLUMN bibtex_key TEXT;

CREATE UNIQUE INDEX idx_source_bibtex_key ON source_document(bibtex_key)
  WHERE bibtex_key IS NOT NULL;
