-- Migration 008 — garantie d'isolation réseau persistée (US-004, ADR-005).
--
-- ADR-005 impose que `network_isolation_guaranteed` soit « persisté et
-- affiché », et le contrat `openapi.yaml` le déclare OBLIGATOIRE dans
-- `ExecutionResult` — y compris dans l'historique des exécutions. Or US-004
-- ne persistait que le niveau (« wasm » / « native »).
--
-- Le déduire du niveau serait un MENSONGE dans un cas précis : au niveau 2
-- sous Linux, l'isolation dépend de la réussite de `unshare -n`. Deux
-- exécutions natives du même poste peuvent donc différer, et seule celle qui
-- a réellement obtenu le namespace peut se dire isolée. On enregistre donc ce
-- qui a été OBSERVÉ à l'exécution, jamais ce qu'on peut en réinférer.
--
-- Valeur par défaut 0 : une exécution antérieure à cette migration n'a pas
-- déclaré sa garantie, et l'absence de preuve se lit « non garanti », jamais
-- « garanti ».
--
-- `timed_out` et `limit_exceeded` suivent la même logique : le contrat les
-- expose dans l'historique. Ne pas les persister obligerait soit à les taire,
-- soit à répondre « false » pour une exécution qui a bel et bien été
-- interrompue — une information fausse plutôt qu'absente.

ALTER TABLE code_execution
  ADD COLUMN network_isolation_guaranteed INTEGER NOT NULL DEFAULT 0
  CHECK (network_isolation_guaranteed IN (0,1));

ALTER TABLE code_execution
  ADD COLUMN timed_out INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0,1));

ALTER TABLE code_execution
  ADD COLUMN limit_exceeded TEXT
  CHECK (limit_exceeded IS NULL OR limit_exceeded IN ('memory','cpu','wall'));
