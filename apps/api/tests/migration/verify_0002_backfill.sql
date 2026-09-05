DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM identity.external_identities
    WHERE issuer = 'https://legacy.example' AND subject = 'LegacySubject'
  ) THEN
    RAISE EXCEPTION 'TASK-002 external identity was not preserved';
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'identity' AND table_name = 'users'
      AND column_name IN ('external_identity_issuer', 'external_identity_subject')
  ) THEN
    RAISE EXCEPTION 'legacy external identity columns still exist';
  END IF;
END
$$;
