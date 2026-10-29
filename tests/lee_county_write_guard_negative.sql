\set ON_ERROR_STOP on

-- Negative write-guard integration test.
-- Required psql variables:
--   -v guard_test_isolated=1
--   -v guard_test_project_ref=ltwqgkcblvtscgldxvjw
--
-- The project-ref value is an explicit operator confirmation; it does not
-- independently prove the remote server identity. Verify the connection host.

\if :{?guard_test_isolated}
\else
    \echo 'ERROR: Missing -v guard_test_isolated=1'
    \quit 2
\endif

\if :{?guard_test_project_ref}
\else
    \echo 'ERROR: Missing -v guard_test_project_ref=<staging-project-ref>'
    \quit 2
\endif

SELECT set_config(
    'god_mode.guard_test_isolated',
    :'guard_test_isolated',
    false
);

SELECT set_config(
    'god_mode.guard_test_project_ref',
    :'guard_test_project_ref',
    false
);

BEGIN;

DO $preflight$
DECLARE
    db_name text := current_database();
    isolated_flag text :=
        current_setting('god_mode.guard_test_isolated', true);
    project_ref text :=
        current_setting('god_mode.guard_test_project_ref', true);
    staging_opt_in boolean;
    missing_columns text;
BEGIN
    staging_opt_in :=
        isolated_flag = '1'
        AND project_ref = 'ltwqgkcblvtscgldxvjw';

    -- "postgres" is permitted only with both explicit staging opt-ins.
    -- Other protected database names remain blocked unconditionally.
    IF db_name IN ('production', 'prod', 'god_mode_prod')
       OR (
           db_name = 'postgres'
           AND NOT staging_opt_in
       )
    THEN
        RAISE EXCEPTION
            'Refusing to run write-guard test on database name: %',
            db_name;
    END IF;

    IF to_regprocedure(
        'god_mode_ops.require_db_writes_enabled()'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Missing guard helper: god_mode_ops.require_db_writes_enabled()';
    END IF;

    IF to_regclass('god_mode_ops.write_control') IS NULL THEN
        RAISE EXCEPTION
            'Missing guard control table: god_mode_ops.write_control';
    END IF;

    SELECT string_agg(required.column_name, ', ')
      INTO missing_columns
      FROM (
          VALUES ('id'), ('db_writes_enabled')
      ) AS required(column_name)
     WHERE NOT EXISTS (
         SELECT 1
           FROM information_schema.columns c
          WHERE c.table_schema = 'god_mode_ops'
            AND c.table_name = 'write_control'
            AND c.column_name = required.column_name
      );

    IF missing_columns IS NOT NULL THEN
        RAISE EXCEPTION
            'Missing required write_control columns: %',
            missing_columns;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM god_mode_ops.write_control
         WHERE id = 1
    ) THEN
        RAISE EXCEPTION
            'Missing god_mode_ops.write_control row with id = 1';
    END IF;

    IF to_regprocedure(
        'god_mode_ops.run_lee_gis_queue(integer,boolean)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Missing function: god_mode_ops.run_lee_gis_queue(integer,boolean)';
    END IF;

    IF to_regprocedure(
        'god_mode_ops.run_county_automation(integer,boolean)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Missing function: god_mode_ops.run_county_automation(integer,boolean)';
    END IF;

    IF to_regprocedure(
        'god_mode_ops.run_county_gis_queue(text,integer,boolean)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Missing function: god_mode_ops.run_county_gis_queue(text,integer,boolean)';
    END IF;

    IF to_regprocedure(
        'god_mode_ops.refresh_county_qualification()'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Missing function: god_mode_ops.refresh_county_qualification()';
    END IF;

    IF to_regprocedure(
        'god_mode_ops.materialize_county_qualified_leads()'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Missing function: god_mode_ops.materialize_county_qualified_leads()';
    END IF;

    RAISE NOTICE 'Preflight passed for database "%".', db_name;
END;
$preflight$;

DO $disable_gate$
DECLARE
    affected_rows bigint;
BEGIN
    UPDATE god_mode_ops.write_control
       SET db_writes_enabled = false
     WHERE id = 1;

    GET DIAGNOSTICS affected_rows = ROW_COUNT;

    IF affected_rows <> 1 THEN
        RAISE EXCEPTION
            'Expected to disable exactly one write-control row; updated %',
            affected_rows;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM god_mode_ops.write_control
         WHERE id = 1
           AND db_writes_enabled IS DISTINCT FROM false
    ) THEN
        RAISE EXCEPTION 'Write-control row did not remain disabled';
    END IF;

    RAISE NOTICE 'Write gate disabled within test transaction.';
END;
$disable_gate$;

-- Each target call is isolated in a PL/pgSQL subtransaction. The test passes
-- only when every function raises SQLSTATE 55000 and exact message
-- DB_WRITES_DISABLED.
DO $assertions$
DECLARE
    target record;
    actual_state text;
    actual_message text;
    passed integer := 0;
BEGIN
    FOR target IN
        SELECT *
          FROM (
              VALUES
              (
                  'god_mode_ops.run_lee_gis_queue(integer,boolean)',
                  'SELECT god_mode_ops.run_lee_gis_queue(2, false)'
              ),
              (
                  'god_mode_ops.run_county_automation(integer,boolean)',
                  'SELECT god_mode_ops.run_county_automation(2, false)'
              ),
              (
                  'god_mode_ops.run_county_gis_queue(text,integer,boolean)',
                  'SELECT god_mode_ops.run_county_gis_queue(NULL, 2, false)'
              ),
              (
                  'god_mode_ops.refresh_county_qualification()',
                  'SELECT god_mode_ops.refresh_county_qualification()'
              ),
              (
                  'god_mode_ops.materialize_county_qualified_leads()',
                  'SELECT god_mode_ops.materialize_county_qualified_leads()'
              )
          ) AS t(signature, invocation)
    LOOP
        actual_state := NULL;
        actual_message := NULL;

        BEGIN
            EXECUTE target.invocation;

            -- Normal return is a test failure. Use a unique message so it
            -- cannot be mistaken for the expected guard exception.
            RAISE EXCEPTION
                USING
                    ERRCODE = 'P0001',
                    MESSAGE = 'ASSERTION_FAILED: ' ||
                              target.signature ||
                              ' unexpectedly succeeded';
        EXCEPTION
            WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS
                    actual_state = RETURNED_SQLSTATE,
                    actual_message = MESSAGE_TEXT;

                IF actual_state = 'P0001'
                   AND actual_message =
                       'ASSERTION_FAILED: ' ||
                       target.signature ||
                       ' unexpectedly succeeded'
                THEN
                    RAISE;
                END IF;
        END;

        IF actual_state IS DISTINCT FROM '55000'
           OR actual_message IS DISTINCT FROM 'DB_WRITES_DISABLED'
        THEN
            RAISE EXCEPTION
                'FAIL: % expected SQLSTATE 55000 / DB_WRITES_DISABLED; got % / %',
                target.signature,
                actual_state,
                actual_message;
        END IF;

        passed := passed + 1;

        RAISE NOTICE
            'PASS (%/5): % returned SQLSTATE % / %',
            passed,
            target.signature,
            actual_state,
            actual_message;
    END LOOP;

    IF passed <> 5 THEN
        RAISE EXCEPTION
            'Expected 5 write-guard assertions to pass; passed %',
            passed;
    END IF;

    RAISE NOTICE 'RESULT: all 5 write-guard assertions passed.';
END;
$assertions$;

ROLLBACK;

\echo 'RESULT: write-guard test transaction rolled back.'
