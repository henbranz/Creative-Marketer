INSERT INTO audit.audit_records (
  id, scope_kind, tenant_id, actor_kind, actor_id, action, outcome,
  correlation_id, environment, tool_call_id, safe_metadata, audit_schema_version
) VALUES (
  '00000000-0000-0000-0000-000000000010', 'platform', NULL, 'system',
  'migration-test', 'tool.execution.succeeded', 'success',
  '00000000-0000-0000-0000-000000000010', 'test',
  '00000000-0000-0000-0000-000000000010', '{}', 1
);
