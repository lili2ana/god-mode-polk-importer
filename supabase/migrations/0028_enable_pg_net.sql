-- Restore pg_net catalog dependencies in ephemeral Supabase migration replay.
-- This extension provides net.http_request_queue and net._http_response
-- referenced by the historical transport-hardening migration.
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;
