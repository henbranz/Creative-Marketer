INSERT INTO identity.users (id, email, normalized_email, status)
VALUES ('00000000-0000-0000-0000-000000000004', 'lossy@example.test', 'lossy@example.test', 'active');

INSERT INTO identity.external_identities (id, user_id, issuer, subject, status)
VALUES
  ('00000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000004', 'https://one.example', 'one', 'active'),
  ('00000000-0000-0000-0000-000000000006', '00000000-0000-0000-0000-000000000004', 'https://two.example', 'two', 'active');
