-- Add one independent credential without rotating the already verified DD/CRM tokens.
ALTER TABLE god_mode_ops.internal_function_credentials DROP CONSTRAINT internal_function_credentials_scope_check;
ALTER TABLE god_mode_ops.internal_function_credentials ADD CONSTRAINT internal_function_credentials_scope_check
CHECK (scope IN ('god-mode-dd-worker','god-mode-dd-finalize','god-mode-crm-feed','god-mode-title-access-worker','god-mode-dashboard'));
DO $setup$
DECLARE token text;
BEGIN
 token := encode(extensions.gen_random_bytes(32),'hex');
 PERFORM vault.create_secret(token,'internal_god_mode_dashboard','Scoped private dashboard authentication');
 INSERT INTO god_mode_ops.internal_function_credentials(scope,token_digest)
 VALUES ('god-mode-dashboard',encode(extensions.digest(token,'sha256'),'hex'));
END $setup$;

-- Preserve schedules. The dispatcher exposes dashboard authentication checks only.
CREATE OR REPLACE FUNCTION god_mode_ops.invoke_internal_worker(p_scope text,p_auth_check boolean DEFAULT false,p_limit integer DEFAULT 5)
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path=''
AS $invoke$
DECLARE token text; request_id bigint; endpoint text; reply extensions.http_response; body jsonb; safe_result jsonb; old_timeout text;
BEGIN
 IF p_scope IS NULL OR p_scope NOT IN ('god-mode-dd-worker','god-mode-dd-finalize','god-mode-crm-feed','god-mode-title-access-worker','god-mode-dashboard')
 OR p_auth_check IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 5 THEN
   RAISE EXCEPTION 'Invalid internal request';
 END IF;
 IF p_scope IN ('god-mode-crm-feed','god-mode-dashboard') AND NOT p_auth_check THEN RAISE EXCEPTION 'Private reads are not scheduled workers'; END IF;
 IF NOT pg_try_advisory_xact_lock(732190403) THEN RAISE EXCEPTION 'Internal worker already running'; END IF;
 SELECT v.decrypted_secret INTO STRICT token FROM vault.decrypted_secrets v
 JOIN god_mode_ops.internal_function_credentials c ON c.scope=p_scope AND c.enabled
 AND c.token_digest=encode(extensions.digest(v.decrypted_secret,'sha256'),'hex')
 WHERE v.name='internal_' || replace(p_scope,'-','_');
 endpoint := 'https://bnsmnztxkqmphvbikaxh.supabase.co/functions/v1/' || p_scope ||
   CASE WHEN p_auth_check THEN '?check=auth' ELSE '?limit=' || p_limit::text END;
 request_id := nextval('god_mode_ops.internal_function_request_seq'::regclass);
 SELECT value INTO old_timeout FROM extensions.http_list_curlopt() WHERE curlopt='CURLOPT_TIMEOUT_MS';
 PERFORM extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS','45000');
 BEGIN
   SELECT * INTO reply FROM extensions.http((
     (CASE WHEN p_scope IN ('god-mode-crm-feed','god-mode-dashboard') THEN 'GET' ELSE 'POST' END)::extensions.http_method,
     endpoint,ARRAY[extensions.http_header('x-god-mode-token',token)],
     'application/json',CASE WHEN p_scope IN ('god-mode-crm-feed','god-mode-dashboard') THEN NULL ELSE '{}' END
   )::extensions.http_request);
   body := reply.content::jsonb;
   safe_result := jsonb_build_object('ok',coalesce(reply.status=200 AND body->>'ok'='true' AND
     CASE WHEN p_auth_check THEN body->>'scope'=p_scope AND body->>'authorized'='true' AND body->>'side_effects'='false'
     ELSE body->>'errors'='0' END,false),
     'processed',body->'processed','errors',body->'errors','authorized',body->'authorized');
 EXCEPTION WHEN OTHERS THEN
   safe_result := '{"ok":false,"error":"transport_or_response_failure","execution_may_have_started":true}'::jsonb;
 END;
 PERFORM extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS',coalesce(old_timeout,'5000'));
 INSERT INTO god_mode_ops.internal_function_requests(request_id,scope,auth_check,queued_at,response_status,result)
   VALUES(request_id,p_scope,p_auth_check,now(),reply.status,safe_result);
 RETURN request_id;
END $invoke$;
REVOKE ALL ON FUNCTION god_mode_ops.invoke_internal_worker(text,boolean,integer) FROM PUBLIC,anon,authenticated,service_role;

