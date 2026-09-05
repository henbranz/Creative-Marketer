INSERT INTO identity.tenants (id, name, slug, status)
VALUES ('00000000-0000-0000-0000-000000000007', 'Permission guard', 'permission-guard', 'active');

INSERT INTO agent_governance.agent_definitions (
  id, scope_kind, tenant_id, agent_key, agent_type, status,
  created_by_actor_kind, created_by_actor_id
) VALUES (
  '00000000-0000-0000-0000-000000000007', 'tenant',
  '00000000-0000-0000-0000-000000000007', 'guard_agent', 'researcher', 'active',
  'user', '00000000-0000-0000-0000-000000000007'
);

INSERT INTO tool_governance.tool_definitions (
  id, tool_key, category, status, created_by_actor_kind, created_by_actor_id
) VALUES (
  '00000000-0000-0000-0000-000000000007', 'demo.permission.guard', 'demo', 'active',
  'system', '00000000-0000-0000-0000-000000000007'
);

INSERT INTO permission_governance.tool_permissions (
  id, tenant_id, agent_definition_id, tool_definition_id, status,
  created_by_actor_kind, created_by_actor_id
) VALUES (
  '00000000-0000-0000-0000-000000000007',
  '00000000-0000-0000-0000-000000000007',
  '00000000-0000-0000-0000-000000000007',
  '00000000-0000-0000-0000-000000000007',
  'active', 'user', '00000000-0000-0000-0000-000000000007'
);
