INSERT INTO identity.tenants (id, name, slug, status)
VALUES ('00000000-0000-0000-0000-000000000009', 'Event downgrade guard', 'event-downgrade-guard', 'active');

INSERT INTO event_delivery.outbox_events (
  event_id, event_type, schema_version, scope_kind, tenant_id,
  aggregate_type, aggregate_id, occurred_at, actor_kind, actor_id,
  correlation_id, payload, payload_schema_digest, event_digest,
  canonicalization_version, publication_state, attempt_count, next_attempt_at,
  created_at, updated_at
) VALUES (
  '00000000-0000-0000-0000-000000000009', 'governance.approval.granted.v1', 1,
  'tenant', '00000000-0000-0000-0000-000000000009', 'approval_request',
  '00000000-0000-0000-0000-000000000009', now(), 'system',
  '00000000-0000-0000-0000-000000000009', '00000000-0000-0000-0000-000000000009',
  '{}', 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  1, 'PENDING', 0, now(), now(), now()
);
