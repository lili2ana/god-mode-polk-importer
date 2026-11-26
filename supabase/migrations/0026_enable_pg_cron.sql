-- Restore pg_cron catalog in clean ephemeral Supabase databases.
-- Do not fabricate historical job definitions: the legacy migration
-- intentionally validates the two pre-existing schedules before modifying them.
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
