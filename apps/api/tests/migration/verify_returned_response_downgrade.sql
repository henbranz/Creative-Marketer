\set ON_ERROR_STOP on

DO $$
DECLARE
    changed_historical_rows integer;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'agent_runtime'
          AND table_name = 'model_attempts'
          AND column_name IN (
              'provider_response_status',
              'provider_failure_reason',
              'usage_available'
          )
    ) THEN
        RAISE EXCEPTION '0033 downgrade retained returned-response columns';
    END IF;

    SELECT count(*) INTO changed_historical_rows
    FROM migration_0033_test.model_attempts_before AS before
    JOIN agent_runtime.model_attempts AS attempt USING (id)
    WHERE before.evidence <> to_jsonb(attempt);
    IF changed_historical_rows <> 0 THEN
        RAISE EXCEPTION '0033 downgrade changed historical ModelAttempt evidence';
    END IF;

    BEGIN
        UPDATE agent_runtime.model_attempts
        SET estimated_cost = estimated_cost + 1
        WHERE id = '00000000-0000-0000-0000-000000003320';
        RAISE EXCEPTION '0032 guard was not restored by downgrade';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'ModelAttempt update requires a lifecycle transition' THEN
            RAISE;
        END IF;
    END;
END
$$;
