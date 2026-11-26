-- Restore the synchronous HTTP extension used by the historical private worker.
-- The extension defines extensions.http_response and extensions.http_request.
-- No network requests are executed by this migration.
CREATE EXTENSION IF NOT EXISTS http WITH SCHEMA extensions;
