BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

-- Exact residential/land inventory counts should not scan wide property rows.
-- The included id also covers the PostgREST head/select-id count shape.
CREATE INDEX properties_property_type_count_idx
  ON public.properties (property_type) INCLUDE (id);

COMMIT;
