-- pg_net request headers may contain Vault credentials; responses may contain DD.
-- Preserve extension-owner/admin access while removing ordinary role access.
-- On hosted Supabase this attempt warned without changing supabase_admin grants.
-- The following migration removes our use of pg_net and rotates credentials.
REVOKE ALL ON TABLE net.http_request_queue,net._http_response FROM PUBLIC,anon,authenticated;
