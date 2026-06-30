-- Phase 16: GIN full-text index for the hybrid keyword leg of kb_search.
--
-- kb_search now ORs a cosine-distance match with a literal keyword match
-- (to_tsvector('simple', content) @@ websearch_to_tsquery('simple', query)) so
-- terse/single-word queries like "education" are found even when the dense
-- vector scores them beyond the distance gate (a 1-word query embeds far from
-- any prose passage). This functional GIN index keeps that keyword leg fast as
-- KBs grow. 'simple' config = language-agnostic tokenisation (RU+EN, no stemming)
-- and is a fixed literal in the query so the planner can match this index.
--
-- Idempotent: IF NOT EXISTS.

CREATE INDEX IF NOT EXISTS idx_kb_chunks_content_fts
    ON kb_chunks USING gin (to_tsvector('simple', content));
