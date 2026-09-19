INSERT INTO identity.tenants (id, name, slug, status)
VALUES (
  '00000000-0000-0000-0000-000000000027',
  'Commerce downgrade',
  'commerce-downgrade',
  'active'
);

INSERT INTO commerce.connections (
  id,
  tenant_id,
  provider,
  display_name,
  external_store_id,
  safe_store_identifier,
  status,
  capabilities,
  created_at,
  updated_at
)
VALUES (
  '00000000-0000-0000-0000-000000000027',
  '00000000-0000-0000-0000-000000000027',
  'fake',
  'Downgrade Fake Store',
  'fake-downgrade-store',
  'fake.local',
  'ACTIVE',
  '["catalog.read"]',
  now(),
  now()
);

INSERT INTO commerce.product_observations (
  id,
  tenant_id,
  connection_id,
  external_product_id,
  title,
  status,
  provider,
  provider_version,
  source_digest,
  schema_version,
  captured_at
)
VALUES (
  '00000000-0000-0000-0000-000000000027',
  '00000000-0000-0000-0000-000000000027',
  '00000000-0000-0000-0000-000000000027',
  'fake-product-downgrade',
  'Commerce downgrade fact',
  'ACTIVE',
  'fake',
  'fake-commerce-v1',
  'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  1,
  now()
);
