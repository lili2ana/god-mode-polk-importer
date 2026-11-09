-- Ensure the private operations schema exists before Judy screening migration 002.
CREATE SCHEMA IF NOT EXISTS god_mode_ops;
REVOKE ALL ON SCHEMA god_mode_ops FROM PUBLIC, anon, authenticated;
