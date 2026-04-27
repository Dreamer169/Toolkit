-- ────────────────────────────────────────────────────────────────────
-- Tag normalization helper + trigger (fork v8.36 fix for ,, corruption)
-- ────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION normalize_tags(input text) RETURNS text AS $$
DECLARE
  result text;
BEGIN
  IF input IS NULL THEN RETURN NULL; END IF;
  WITH split AS (
    SELECT trim(t) AS t, MIN(ord) AS first_ord
      FROM unnest(string_to_array(input, ',')) WITH ORDINALITY AS x(t, ord)
     WHERE trim(t) <> ''
     GROUP BY trim(t)
  )
  SELECT NULLIF(string_agg(t, ',' ORDER BY first_ord), '')
    INTO result
    FROM split;
  RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION accounts_normalize_tags_trg() RETURNS trigger AS $$
BEGIN
  NEW.tags := normalize_tags(NEW.tags);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS accounts_normalize_tags ON accounts;
CREATE TRIGGER accounts_normalize_tags
  BEFORE INSERT OR UPDATE ON accounts
  FOR EACH ROW EXECUTE FUNCTION accounts_normalize_tags_trg();

-- One-shot historical cleanup (no-op if already clean)
UPDATE accounts SET tags = normalize_tags(tags)
 WHERE tags IS NOT NULL AND tags <> COALESCE(normalize_tags(tags), '');

-- Verify
SELECT 'function exists' AS check, EXISTS(SELECT 1 FROM pg_proc WHERE proname='normalize_tags') AS ok
UNION ALL SELECT 'trigger exists', EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='accounts_normalize_tags');

-- Self-test
SELECT 'self-test 1 (double comma)' AS case, normalize_tags('replit_used,,inbox_error') AS result, 'replit_used,inbox_error' AS expected
UNION ALL SELECT 'self-test 2 (leading comma)', normalize_tags(',token_invalid'), 'token_invalid'
UNION ALL SELECT 'self-test 3 (no separator)', normalize_tags('replit_usedsubnode_deployed'), 'replit_usedsubnode_deployed'
UNION ALL SELECT 'self-test 4 (dup)', normalize_tags('replit_used,token_invalid,replit_used'), 'replit_used,token_invalid'
UNION ALL SELECT 'self-test 5 (empty)', COALESCE(normalize_tags(''),'<NULL>'), '<NULL>'
UNION ALL SELECT 'self-test 6 (preserve order)', normalize_tags('inbox_verified,replit_used,abuse_mode'), 'inbox_verified,replit_used,abuse_mode';

-- Show current dirty rows (should be 0)
SELECT count(*) AS still_dirty FROM accounts
 WHERE tags ~ ',,' OR tags ~ '^,' OR tags ~ ',$' OR tags ~ '(,|^)(replit_used|inbox_verified|token_invalid|inbox_error|abuse_mode|subnode_deployed)(.*?)\1';
