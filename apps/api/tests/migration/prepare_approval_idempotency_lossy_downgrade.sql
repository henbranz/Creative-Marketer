INSERT INTO audit.audit_records (
  id, scope_kind, tenant_id, actor_kind, actor_id, action, outcome,
  correlation_id, environment, safe_metadata, audit_schema_version,
  idempotency_record_id
) VALUES (
  '00000000-0000-0000-0000-000000000008', 'platform', NULL, 'system', NULL,
  'migration.approval_idempotency.guard', 'success',
  '00000000-0000-0000-0000-000000000008', 'test', '{}'::jsonb, 1,
  '00000000-0000-0000-0000-000000000008'
);
