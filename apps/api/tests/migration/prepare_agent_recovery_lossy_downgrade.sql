INSERT INTO identity.tenants (id, name, slug, status)
VALUES ('00000000-0000-0000-0000-000000000016', 'Recovery downgrade', 'recovery-downgrade', 'active');

INSERT INTO agent_governance.agent_definitions
    (id, scope_kind, tenant_id, agent_key, agent_type, status,
     created_by_actor_kind, created_by_actor_id)
VALUES
    ('00000000-0000-0000-0000-000000000016', 'tenant',
     '00000000-0000-0000-0000-000000000016', 'recovery_guard', 'researcher', 'active',
     'system', '00000000-0000-0000-0000-000000000016');

INSERT INTO agent_runtime.agent_budget_usage
    (tenant_id, agent_definition_id, period_start, currency, reserved_runs,
     reserved_cost, actual_cost, unknown_cost)
VALUES
    ('00000000-0000-0000-0000-000000000016',
     '00000000-0000-0000-0000-000000000016', '2026-09-12T00:00:00Z', 'USD', 1, 0, 0, 1);
