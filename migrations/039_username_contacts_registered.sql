-- 039: контакты с username считаются заведомо зарегистрированными.
--
-- Next free migration number is 039 (038_warmup_settings.sql is the previous one).
-- Auto-applied at api start by app/database.py::_apply_migrations in lexical order;
-- this file MUST be idempotent — the applier re-runs it on any schema drift and the
-- api fail-fasts (does not start) if a migration raises.
--
-- Why: наличие @username = достаточный сигнал, что аккаунт существует и ему можно
-- написать напрямую (ResolveUsername), поэтому phone-resolve через checker для таких
-- контактов лишний и только жжёт лимиты чекера. Новые контакты теперь импортятся с
-- tg_status='registered' сразу (см. app/routers/contacts.py::_insert_contacts_with_dedup).
-- Этот бэкфилл приводит уже лежащие в базе НЕРАЗРЕШЁННЫЕ контакты с username к тому же
-- правилу: переводит 'pending'/'unchecked' → 'registered'.
--
-- Идемпотентность: повторный прогон не находит строк (после первого прогона у них уже
-- 'registered'). Терминальные статусы НЕ трогаем — 'not_registered'/'error' могли быть
-- проставлены осознанно резолвом по username, перетирать их нельзя.
UPDATE contacts
   SET tg_status = 'registered',
       updated_at = NOW()
 WHERE tg_status IN ('pending', 'unchecked')
   AND username IS NOT NULL
   AND username <> '';
