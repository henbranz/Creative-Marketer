\set ON_ERROR_STOP on

DO $$
DECLARE
    changed_historical_rows integer;
    metadata_mismatches integer;
BEGIN
    SELECT count(*) INTO changed_historical_rows
    FROM migration_0033_test.model_attempts_before AS before
    JOIN agent_runtime.model_attempts AS attempt USING (id)
    WHERE before.evidence <> (
        to_jsonb(attempt) - ARRAY[
            'provider_response_status',
            'provider_failure_reason',
            'usage_available'
        ]
    );
    IF changed_historical_rows <> 0 THEN
        RAISE EXCEPTION '0033 changed pre-existing ModelAttempt evidence';
    END IF;

    SELECT count(*) INTO metadata_mismatches
    FROM agent_runtime.model_attempts
    WHERE id IN (
        '00000000-0000-0000-0000-000000003320',
        '00000000-0000-0000-0000-000000003321'
    )
    AND NOT (
        status IN ('SUCCEEDED', 'RESPONSE_RECORDED')
        AND provider_response_status = 'completed'
        AND provider_failure_reason IS NULL
        AND usage_available IS TRUE
    );
    IF metadata_mismatches <> 0 THEN
        RAISE EXCEPTION '0033 did not backfill completed response metadata exactly';
    END IF;

    SELECT count(*) INTO metadata_mismatches
    FROM agent_runtime.model_attempts
    WHERE id IN (
        '00000000-0000-0000-0000-000000003322',
        '00000000-0000-0000-0000-000000003323'
    )
    AND NOT (
        status IN ('UNKNOWN', 'FAILED_NO_RESPONSE')
        AND provider_response_status IS NULL
        AND provider_failure_reason IS NULL
        AND usage_available IS NULL
    );
    IF metadata_mismatches <> 0 THEN
        RAISE EXCEPTION '0033 changed no-response or unknown response metadata';
    END IF;

    IF (SELECT unknown_cost FROM agent_runtime.model_attempts
        WHERE id = '00000000-0000-0000-0000-000000003322') <> 0.256000 THEN
        RAISE EXCEPTION '0033 changed historical unknown cost';
    END IF;

    IF position(
        'to_jsonb(NEW)' IN pg_get_functiondef(
            'agent_runtime.protect_model_attempt()'::regprocedure
        )
    ) <> 0 THEN
        RAISE EXCEPTION '0033 left the transitional migration guard installed';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_proc AS procedure
        CROSS JOIN LATERAL aclexplode(procedure.proacl) AS privilege
        WHERE procedure.oid = 'agent_runtime.protect_model_attempt()'::regprocedure
          AND privilege.grantee = 0
          AND privilege.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION '0033 granted public execution on protect_model_attempt';
    END IF;
END
$$;

-- The final guard must reject every mutation outside normal lifecycle transitions.
DO $$
BEGIN
    BEGIN
        UPDATE agent_runtime.model_attempts
        SET estimated_cost = estimated_cost + 1
        WHERE id = '00000000-0000-0000-0000-000000003321';
        RAISE EXCEPTION 'final guard allowed arbitrary same-status mutation';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'ModelAttempt update requires a lifecycle transition' THEN
            RAISE;
        END IF;
    END;

    BEGIN
        UPDATE agent_runtime.model_attempts
        SET failure_code = 'TAMPERED'
        WHERE id = '00000000-0000-0000-0000-000000003320';
        RAISE EXCEPTION 'final guard allowed terminal-row mutation';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'ModelAttempt update requires a lifecycle transition' THEN
            RAISE;
        END IF;
    END;

    BEGIN
        UPDATE agent_runtime.model_attempts
        SET status = 'CLAIMED'
        WHERE id = '00000000-0000-0000-0000-000000003321';
        RAISE EXCEPTION 'final guard allowed invalid lifecycle transition';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'invalid ModelAttempt transition' THEN
            RAISE;
        END IF;
    END;

    BEGIN
        UPDATE agent_runtime.model_attempts
        SET workload_id = 'tampered-workload'
        WHERE id = '00000000-0000-0000-0000-000000003321';
        RAISE EXCEPTION 'final guard allowed identity mutation';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'ModelAttempt identity is immutable' THEN
            RAISE;
        END IF;
    END;
END
$$;
