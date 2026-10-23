BEGIN;

-- A fixed, service-only boolean lookup. No Auth rows are exposed to API users.
-- SECURITY DEFINER is needed because service_role has no direct Auth-table
-- grants here. The handler separately verifies the JWT and operator UUID.
CREATE OR REPLACE FUNCTION public.god_mode_operator_session_active(
  requested_user uuid, requested_session uuid
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM auth.sessions AS s
    JOIN auth.users AS u ON u.id = s.user_id
    WHERE s.id = requested_session
      AND s.user_id = requested_user
      AND (s.not_after IS NULL OR s.not_after > statement_timestamp())
      AND u.email_confirmed_at IS NOT NULL
      AND u.is_anonymous IS FALSE
      AND u.deleted_at IS NULL
      AND (u.banned_until IS NULL OR u.banned_until <= statement_timestamp())
  );
$$;

REVOKE ALL ON FUNCTION public.god_mode_operator_session_active(uuid, uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.god_mode_operator_session_active(uuid, uuid)
  TO service_role;

COMMIT;
