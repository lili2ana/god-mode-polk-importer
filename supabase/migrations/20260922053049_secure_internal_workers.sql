-- Generated credentials never leave PostgreSQL/Vault in query output or source.
CREATE TABLE god_mode_ops.internal_function_credentials (
  scope text PRIMARY KEY CHECK (scope IN ('god-mode-dd-worker','god-mode-dd-finalize','god-mode-crm-feed')),
  token_digest text NOT NULL CHECK (token_digest ~ '^[a-f0-9]{64}$'),
  enabled boolean NOT NULL DEFAULT true
);
ALTER TABLE god_mode_ops.internal_function_credentials ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.internal_function_credentials FROM PUBLIC,anon,authenticated,service_role;
DO $setup$
DECLARE scope_name text; token text;
BEGIN
  FOREACH scope_name IN ARRAY ARRAY['god-mode-dd-worker','god-mode-dd-finalize','god-mode-crm-feed'] LOOP
    token := encode(extensions.gen_random_bytes(32),'hex');
    PERFORM vault.create_secret(token,'internal_' || replace(scope_name,'-','_'),'Scoped internal function authentication');
    INSERT INTO god_mode_ops.internal_function_credentials(scope,token_digest)
      VALUES(scope_name,encode(extensions.digest(token,'sha256'),'hex'));
  END LOOP;
END $setup$;

-- Public RPC is reachable only with a service credential. It reads hashes only;
-- its definer privileges cannot access arbitrary scopes/tables supplied by callers.
CREATE FUNCTION public.god_mode_check_internal_token(p_scope text,p_digest text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=''
AS $$ SELECT EXISTS(SELECT 1 FROM god_mode_ops.internal_function_credentials
 WHERE scope=p_scope AND enabled AND token_digest=p_digest AND p_digest ~ '^[a-f0-9]{64}$') $$;
REVOKE ALL ON FUNCTION public.god_mode_check_internal_token(text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.god_mode_check_internal_token(text,text) TO service_role;

CREATE TABLE god_mode_ops.internal_function_requests (
  request_id bigint PRIMARY KEY,scope text NOT NULL,auth_check boolean NOT NULL,
  queued_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE god_mode_ops.internal_function_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON god_mode_ops.internal_function_requests FROM PUBLIC,anon,authenticated,service_role;

CREATE FUNCTION god_mode_ops.invoke_internal_worker(p_scope text,p_auth_check boolean DEFAULT false,p_limit integer DEFAULT 5)
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path=''
AS $invoke$
DECLARE token text; request_id bigint; endpoint text; request_headers jsonb;
BEGIN
 IF p_scope IS NULL OR p_scope NOT IN ('god-mode-dd-worker','god-mode-dd-finalize','god-mode-crm-feed')
 OR p_auth_check IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 5 THEN
   RAISE EXCEPTION 'Invalid internal request';
 END IF;
 IF p_scope='god-mode-crm-feed' AND NOT p_auth_check THEN RAISE EXCEPTION 'CRM data fetch is not a scheduled worker'; END IF;
 SELECT v.decrypted_secret INTO STRICT token FROM vault.decrypted_secrets v
 JOIN god_mode_ops.internal_function_credentials c ON c.scope=p_scope AND c.enabled
 AND c.token_digest=encode(extensions.digest(v.decrypted_secret,'sha256'),'hex')
 WHERE v.name='internal_' || replace(p_scope,'-','_');
 endpoint := 'https://bnsmnztxkqmphvbikaxh.supabase.co/functions/v1/' || p_scope ||
   CASE WHEN p_auth_check THEN '?check=auth' ELSE '?limit=' || p_limit::text END;
 request_headers := jsonb_build_object('Content-Type','application/json','x-god-mode-token',token);
 IF p_scope='god-mode-crm-feed' THEN
   request_id := net.http_get(url:=endpoint,headers:=request_headers,timeout_milliseconds:=10000);
 ELSE
   request_id := net.http_post(url:=endpoint,body:='{}'::jsonb,headers:=request_headers,timeout_milliseconds:=120000);
 END IF;
 INSERT INTO god_mode_ops.internal_function_requests VALUES(request_id,p_scope,p_auth_check,now());
 RETURN request_id;
END $invoke$;
REVOKE ALL ON FUNCTION god_mode_ops.invoke_internal_worker(text,boolean,integer) FROM PUBLIC,anon,authenticated,service_role;

DO $cron$
DECLARE worker_job bigint; finalizer_job bigint;
BEGIN
 SELECT jobid INTO STRICT worker_job FROM cron.job WHERE jobname='god_mode_dd_worker_daily' AND username='postgres';
 SELECT jobid INTO STRICT finalizer_job FROM cron.job WHERE jobname='god_mode_dd_finalize_daily' AND username='postgres';
 PERFORM cron.alter_job(worker_job,command:=$cmd$select god_mode_ops.invoke_internal_worker('god-mode-dd-worker');$cmd$);
 PERFORM cron.alter_job(finalizer_job,command:=$cmd$select god_mode_ops.invoke_internal_worker('god-mode-dd-finalize');$cmd$);
END $cron$;
