-- Migration 002 — contrainte d'unicité du chaînage d'audit (US-701, ADR-009).
--
-- Lire le dernier hash d'un projet puis insérer n'est atomique que si la
-- séquence est sérialisée. Un verrou applicatif y pourvoit, mais un verrou
-- ne protège que le processus qui le détient : deux instances du backend,
-- un script d'import ou une reprise après incident produiraient une fourche
-- silencieuse — deux entrées partageant le même prev_hash, chacune
-- parfaitement valide isolément.
--
-- L'index rend cette fourche impossible à passer inaperçue : elle devient
-- une violation de contrainte au moment de l'écriture, et non une
-- incohérence découverte des mois plus tard lors d'une vérification.

CREATE UNIQUE INDEX idx_audit_prev ON audit_log(project_id, prev_hash);
