\set ON_ERROR_STOP on

-- Minimal, fully constrained 0032 history for the 0033 existing-data upgrade regression.
INSERT INTO identity.tenants (id, name, slug, status)
VALUES ('00000000-0000-0000-0000-000000003301', '0033 upgrade', 'migration-0033', 'active');

INSERT INTO identity.users (id, email, normalized_email, status)
VALUES (
    '00000000-0000-0000-0000-000000003301',
    'migration-0033@example.test',
    'migration-0033@example.test',
    'active'
);

INSERT INTO catalog.brands (id, tenant_id, name, slug, status, created_by)
VALUES (
    '00000000-0000-0000-0000-000000003302',
    '00000000-0000-0000-0000-000000003301',
    '0033 upgrade',
    'migration-0033',
    'active',
    '00000000-0000-0000-0000-000000003301'
);

INSERT INTO catalog.products
    (id, tenant_id, brand_id, name, slug, category, short_description, status, created_by)
VALUES (
    '00000000-0000-0000-0000-000000003303',
    '00000000-0000-0000-0000-000000003301',
    '00000000-0000-0000-0000-000000003302',
    '0033 upgrade product',
    'migration-0033-product',
    'Test',
    'Migration regression fixture',
    'active',
    '00000000-0000-0000-0000-000000003301'
);

INSERT INTO catalog.product_knowledge_snapshots
    (id, tenant_id, product_id, schema_version, source_revision, content, digest,
     created_by, created_at)
VALUES (
    '00000000-0000-0000-0000-000000003304',
    '00000000-0000-0000-0000-000000003301',
    '00000000-0000-0000-0000-000000003303',
    1,
    1,
    '{"fixture":true}'::jsonb,
    'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    '00000000-0000-0000-0000-000000003301',
    '2026-09-25T00:00:00Z'
);

INSERT INTO agent_governance.agent_definitions
    (id, scope_kind, tenant_id, platform_template_id, agent_key, agent_type, status,
     created_by_actor_kind, created_by_actor_id)
VALUES (
    '00000000-0000-0000-0000-000000003305',
    'tenant',
    '00000000-0000-0000-0000-000000003301',
    NULL,
    'migration_0033',
    'researcher',
    'active',
    'system',
    '00000000-0000-0000-0000-000000003301'
);

INSERT INTO agent_governance.agent_versions
    (id, definition_id, scope_kind, tenant_id, version_number, display_name, mission,
     responsibilities, system_instructions, prompt_revision, model_policy,
     run_budget_policy, period_budget_policy, read_scopes, write_scopes, memory_scopes,
     allowed_tool_keys, denied_tool_keys, approval_policy_key, output_contract_key,
     output_contract_version, configuration_schema_version, configuration_digest,
     created_by_actor_kind, created_by_actor_id, created_at)
VALUES (
    '00000000-0000-0000-0000-000000003306',
    '00000000-0000-0000-0000-000000003305',
    'tenant',
    '00000000-0000-0000-0000-000000003301',
    1,
    'Migration 0033',
    'Exercise the existing-data migration path.',
    '["migration regression"]'::jsonb,
    'Use deterministic fixture data only.',
    'migration-0033-v1',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    'migration.regression',
    'migration.output',
    1,
    1,
    'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'system',
    '00000000-0000-0000-0000-000000003301',
    '2026-09-25T00:00:00Z'
);

INSERT INTO agent_runtime.agent_runs
    (id, tenant_id, requested_agent_definition_id, resolved_agent_definition_id,
     agent_version_id, agent_version_number, agent_configuration_digest, prompt_revision,
     product_id, product_snapshot_id, product_snapshot_digest, product_snapshot_schema_version,
     research_context_digest, context_digest, selected_evidence, model_profile_key,
     output_contract_key, output_contract_version, correlation_id, initiated_by_actor_kind,
     initiated_by_actor_id, period_start, reserved_cost, currency, idempotency_key, status,
     created_at, started_at, completed_at, executed_by_workload_id, resolved_provider,
     resolved_model, resolved_model_route_version, pricing_version, reasoning_effort,
     max_output_tokens, max_total_tokens, model_call_count, input_tokens, output_tokens,
     total_tokens, estimated_cost, provider_response_id, result_ref, failure_code,
     recovery_of_run_id, agent_type, input_context_kind, input_context_schema_version,
     input_context_digest, input_context_refs)
VALUES
    ('00000000-0000-0000-0000-000000003310',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003306', 1,
     'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
     'migration-0033-v1',
     '00000000-0000-0000-0000-000000003303',
     '00000000-0000-0000-0000-000000003304',
     'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 1,
     'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"ref":"fixture"}]'::jsonb, 'migration-profile', 'migration.output', 1,
     '00000000-0000-0000-0000-000000003310', 'system',
     '00000000-0000-0000-0000-000000003301', '2026-09-25T00:00:00Z', 0.256, 'USD',
     'migration-0033-succeeded', 'SUCCEEDED', '2026-09-25T00:00:00Z',
     '2026-09-25T00:00:01Z', '2026-09-25T00:00:05Z', 'migration-workload',
     'openai', 'gpt-5.6-sol', 'migration-route', 'migration-pricing', 'high', 8000, 32000,
     1, 100, 50, 150, 0.001400, 'resp-succeeded', 'result-succeeded', NULL, NULL,
     'researcher', 'researcher.v1', 1,
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"kind":"fixture"}]'::jsonb),
    ('00000000-0000-0000-0000-000000003311',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003306', 1,
     'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
     'migration-0033-v1',
     '00000000-0000-0000-0000-000000003303',
     '00000000-0000-0000-0000-000000003304',
     'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 1,
     'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"ref":"fixture"}]'::jsonb, 'migration-profile', 'migration.output', 1,
     '00000000-0000-0000-0000-000000003311', 'system',
     '00000000-0000-0000-0000-000000003301', '2026-09-25T00:00:00Z', 0.256, 'USD',
     'migration-0033-recorded', 'RUNNING', '2026-09-25T00:00:00Z',
     '2026-09-25T00:00:01Z', NULL, 'migration-workload', 'openai', 'gpt-5.6-sol',
     'migration-route', 'migration-pricing', 'high', 8000, 32000, 1, 80, 40, 120,
     0.001120, 'resp-recorded', NULL, NULL, NULL, 'researcher', 'researcher.v1', 1,
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"kind":"fixture"}]'::jsonb),
    ('00000000-0000-0000-0000-000000003312',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003306', 1,
     'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
     'migration-0033-v1',
     '00000000-0000-0000-0000-000000003303',
     '00000000-0000-0000-0000-000000003304',
     'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 1,
     'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"ref":"fixture"}]'::jsonb, 'migration-profile', 'migration.output', 1,
     '00000000-0000-0000-0000-000000003312', 'system',
     '00000000-0000-0000-0000-000000003301', '2026-09-25T00:00:00Z', 0.256, 'USD',
     'migration-0033-unknown', 'FAILED', '2026-09-25T00:00:00Z',
     '2026-09-25T00:00:01Z', '2026-09-25T00:00:05Z', 'migration-workload',
     'openai', 'gpt-5.6-sol', 'migration-route', 'migration-pricing', 'high', 8000, 32000,
     1, 0, 0, 0, 0, NULL, NULL, 'STRANDED_PROVIDER_OUTCOME_UNKNOWN', NULL,
     'researcher', 'researcher.v1', 1,
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"kind":"fixture"}]'::jsonb),
    ('00000000-0000-0000-0000-000000003313',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003305',
     '00000000-0000-0000-0000-000000003306', 1,
     'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
     'migration-0033-v1',
     '00000000-0000-0000-0000-000000003303',
     '00000000-0000-0000-0000-000000003304',
     'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 1,
     'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"ref":"fixture"}]'::jsonb, 'migration-profile', 'migration.output', 1,
     '00000000-0000-0000-0000-000000003313', 'system',
     '00000000-0000-0000-0000-000000003301', '2026-09-25T00:00:00Z', 0.256, 'USD',
     'migration-0033-no-response', 'FAILED', '2026-09-25T00:00:00Z',
     '2026-09-25T00:00:01Z', '2026-09-25T00:00:05Z', 'migration-workload',
     'openai', 'gpt-5.6-sol', 'migration-route', 'migration-pricing', 'high', 8000, 32000,
     1, 0, 0, 0, 0, NULL, NULL, 'MODEL_PROVIDER_BAD_REQUEST', NULL,
     'researcher', 'researcher.v1', 1,
     'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
     '[{"kind":"fixture"}]'::jsonb);

INSERT INTO agent_runtime.model_attempts
    (id, tenant_id, agent_run_id, attempt_number, workload_id, model_route_version,
     pricing_version, provider, model, status, claimed_at, provider_started_at,
     response_recorded_at, finished_at, lease_expires_at, provider_response_id,
     input_tokens, output_tokens, total_tokens, estimated_cost, unknown_cost, failure_code)
VALUES
    ('00000000-0000-0000-0000-000000003320',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003310', 1, 'migration-succeeded',
     'migration-route', 'migration-pricing', 'openai', 'gpt-5.6-sol', 'SUCCEEDED',
     '2026-09-25T00:00:00Z', '2026-09-25T00:00:01Z', '2026-09-25T00:00:04Z',
     '2026-09-25T00:00:05Z', '2026-09-25T00:15:00Z', 'resp-succeeded',
     100, 50, 150, 0.001400, 0, NULL),
    ('00000000-0000-0000-0000-000000003321',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003311', 1, 'migration-recorded',
     'migration-route', 'migration-pricing', 'openai', 'gpt-5.6-sol', 'RESPONSE_RECORDED',
     '2026-09-25T00:00:00Z', '2026-09-25T00:00:01Z', '2026-09-25T00:00:04Z',
     NULL, '2026-09-25T00:15:00Z', 'resp-recorded', 80, 40, 120, 0.001120, 0, NULL),
    ('00000000-0000-0000-0000-000000003322',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003312', 1, 'migration-unknown',
     'migration-route', 'migration-pricing', 'openai', 'gpt-5.6-sol', 'UNKNOWN',
     '2026-09-25T00:00:00Z', '2026-09-25T00:00:01Z', NULL,
     '2026-09-25T00:00:05Z', '2026-09-25T00:15:00Z', NULL,
     0, 0, 0, 0, 0.256000, 'MODEL_PROVIDER_INCOMPLETE_RESPONSE'),
    ('00000000-0000-0000-0000-000000003323',
     '00000000-0000-0000-0000-000000003301',
     '00000000-0000-0000-0000-000000003313', 1, 'migration-no-response',
     'migration-route', 'migration-pricing', 'openai', 'gpt-5.6-sol', 'FAILED_NO_RESPONSE',
     '2026-09-25T00:00:00Z', '2026-09-25T00:00:01Z', NULL,
     '2026-09-25T00:00:05Z', '2026-09-25T00:15:00Z', NULL,
     0, 0, 0, 0, 0, 'MODEL_PROVIDER_BAD_REQUEST');

CREATE SCHEMA migration_0033_test;
CREATE TABLE migration_0033_test.model_attempts_before (
    id uuid PRIMARY KEY,
    evidence jsonb NOT NULL
);
INSERT INTO migration_0033_test.model_attempts_before (id, evidence)
SELECT id, to_jsonb(attempt)
FROM agent_runtime.model_attempts AS attempt
WHERE id IN (
    '00000000-0000-0000-0000-000000003320',
    '00000000-0000-0000-0000-000000003321',
    '00000000-0000-0000-0000-000000003322',
    '00000000-0000-0000-0000-000000003323'
);

-- Prove the 0032 immutability trigger is installed and active before Alembic starts 0033.
DO $$
BEGIN
    BEGIN
        UPDATE agent_runtime.model_attempts
        SET estimated_cost = estimated_cost + 1
        WHERE id = '00000000-0000-0000-0000-000000003320';
        RAISE EXCEPTION '0032 ModelAttempt guard unexpectedly allowed same-status mutation';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'ModelAttempt update requires a lifecycle transition' THEN
            RAISE;
        END IF;
    END;
END
$$;
