-- migrations/050_lower_new_dialog_cap.sql
-- Quick 260706-mdz: lower per-account daily new-dialog cap 50 → 10.
-- D-1: change DB default for new campaigns AND update every existing row still at the
--      old default 50 (all 6 prod rows). Manually-set non-50 values are preserved.
-- Idempotent: SET DEFAULT re-applies safely; UPDATE guarded WHERE = 50 (0 rows on re-run).
-- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory lock, fail-fast).
BEGIN;
ALTER TABLE campaigns ALTER COLUMN max_new_dialogs_per_day SET DEFAULT 10;
UPDATE campaigns SET max_new_dialogs_per_day = 10 WHERE max_new_dialogs_per_day = 50;
COMMIT;
