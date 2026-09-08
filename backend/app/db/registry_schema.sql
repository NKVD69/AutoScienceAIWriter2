-- Registre global des projets — US-101.
--
-- Ce fichier ne contient AUCUNE donnée de projet : seulement où les trouver.
-- Un projet vit dans son propre .sqlite (ADR-001), ce qui rend la sauvegarde
-- et la transmission triviales — copier un fichier transporte les sources,
-- les chunks, les vecteurs, le plan, les sections et l'audit.
--
-- `slug` ne change jamais après création, même si le nom du projet change :
-- le chemin du fichier en dépend, et renommer un fichier sous les pieds de
-- l'utilisateur romprait ses propres sauvegardes.

CREATE TABLE IF NOT EXISTS project_ref (
  id          INTEGER PRIMARY KEY,
  slug        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  db_path     TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL,
  last_opened TEXT
);

CREATE INDEX IF NOT EXISTS idx_project_ref_slug ON project_ref(slug);
