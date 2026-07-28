---
slug: recovery-email-password-algo
status: awaiting_human_verify
trigger: |
  Через "edit account" пользователь пытается назначить новую почту для
  восстановления (recovery email). Вводит почту, нажимает "получить код" —
  получает ошибку "unsupported password algorithm NoneType".
created: 2026-07-13
updated: 2026-07-13
---

# Debug Session: recovery-email-password-algo

## Symptoms

- **Expected behavior:** После ввода новой recovery-почты и нажатия "получить
  код" Telegram должен прислать код подтверждения на почту, чтобы завершить
  привязку recovery email к аккаунту.
- **Actual behavior:** Всплывающее уведомление в UI с ошибкой "unsupported
  password algorithm NoneType" вместо отправки кода.
- **Error messages:** "unsupported password algorithm NoneType" — показывается
  всплывающим уведомлением (toast) в UI сразу после нажатия "получить код".
- **Timeline:** Никогда не пробовали раньше — это первая попытка назначить
  recovery email через этот флоу.
- **Reproduction:** В UI открыть edit account → recovery email → ввести новую
  почту → нажать "получить код". Воспроизводится минимум на 2+ аккаунтах
  (проверено на нескольких sender'ах, не только на одном).
- **Additional context:** Неизвестно, стоит ли у затронутых Telegram-аккаунтов
  облачный пароль (2FA) — пользователь не проверял. Название ошибки указывает
  на password algorithm = None, что похоже на случай, когда у аккаунта вообще
  нет установленного 2FA-пароля (Telegram SRP algo отсутствует), а код на
  backend пытается использовать SRP-алгоритм пароля без проверки, что пароль
  вообще существует.

## Current Focus

hypothesis: REOPENED after field test (2026-07-13). User "polina_ags" made
  request, got NO visible error (neither new NO_2FA_PASSWORD 400 nor old
  "unsupported password algorithm" crash) but NO email arrived. Two candidate
  mechanisms: (A) success path of start_recovery_email silently returns
  {"code_length": None} without Telegram actually sending the email; (B)
  has_password=True but current_password wrong/empty → RPC error swallowed.
  Resolving sender + reading API logs first.
test_A: resolve polina_ags → check has_password → read outreach-platform-api logs.
resolution: CONFIRMED root cause = email-only UpdatePasswordSettingsRequest.
  Fix: mirror telethon edit_2fa (randomize new_algo.salt1, include new_algo +
  new_password_hash=compute_digest(same current_password) + hint + email);
  require current_password; and stop the router from falsely reporting
  EMAIL_CONFIRMATION_SENT when no code was actually sent.
next_action: DEPLOY GATED — prod api rebuild pending. app/ working tree ALSO
  carries a parallel agent's in-flight changes (listener.py, queue.py,
  warmup.py, contact_check_worker.py, config.py, models/__init__.py +
  migration 062) for proxy-switch-listener-lag. A `docker compose up -d
  --build api` would ship those too. Did NOT rebuild/commit unilaterally.
  After deploy, user re-tests recovery-email on an account WITH 2FA set →
  code must arrive by email.
self_verify: 22/22 passed in isolated compose project recovv (test-overlay,
  --no-deps, own db-test) — incl. 2 new regression tests capturing the
  full new_settings payload + CURRENT_PASSWORD_REQUIRED guard, and the
  router EMAIL_ALREADY_CONFIRMED path.

prior_hypothesis (CONFIRMED, fix deployed): `TelegramService.start_recovery_email`
  (app/services/telegram.py:1683) безусловно вызывает
  `compute_check(pwd, current_password or "")`. У аккаунта без облачного
  пароля (2FA) `GetPasswordRequest()` возвращает Password с
  `has_password=False` и `current_algo=None`. Telethon `compute_check`
  (telethon/password.py:136-140) делает `algo = request.current_algo` и
  `raise ValueError('unsupported password algorithm {}'.format(
  algo.__class__.__name__))` → algo=None → "unsupported password algorithm
  NoneType". Эта ValueError падает мимо таблицы маппинга в роутере и уходит
  как 500 PROFILE_UPDATE_FAILED с текстом ошибки → toast в UI.
test: прочитан telethon/password.py::compute_check + сигнатура типа
  account.Password (есть поля has_password и current_algo).
expecting: —
next_action: fix — гейт `if not pwd.has_password` в start_recovery_email
  (recovery email нельзя привязать к аккаунту без 2FA-пароля — это его
  механизм восстановления) + структурный маппинг NO_2FA_PASSWORD → 400 в
  роутере. Добавить тесты.

## Eliminated

<!-- none yet -->

## Evidence

- timestamp: 2026-07-13
  checked: app/services/telegram.py::start_recovery_email (строки 1646-1698)
  found: После `GetPasswordRequest()` безусловно вызывается
    `compute_check(pwd, current_password or "")`. Нет проверки, установлен
    ли у аккаунта облачный пароль.
  implication: Для аккаунта без 2FA `compute_check` получит algo=None.

- timestamp: 2026-07-13
  checked: telethon/password.py::compute_check (строки 136-140)
  found: `algo = request.current_algo; if not isinstance(algo,
    PasswordKdfAlgoSHA256...): raise ValueError('unsupported password
    algorithm {}'.format(algo.__class__.__name__))`.
  implication: Когда current_algo=None → algo.__class__.__name__ ==
    "NoneType" → точное сообщение из symptoms воспроизведено. Это ТОЧНАЯ
    строка ошибки, которую видит пользователь.

- timestamp: 2026-07-13
  checked: telethon.tl.types.account.Password сигнатура
  found: Поля включают `has_password` (bool) и `current_algo`. У аккаунта
    без облачного пароля has_password=False и current_algo=None.
  implication: Надёжный гейт — `if not pwd.has_password` (или current_algo
    is None) до вызова compute_check.

- timestamp: 2026-07-13
  checked: app/routers/senders.py::_raise_profile_telegram_error (333-397) +
    start_sender_recovery_email (1662-1699)
  found: ValueError("unsupported password algorithm NoneType") не совпадает
    ни с одним needle в таблице → падает в fallthrough
    `500 PROFILE_UPDATE_FAILED, message=str(e)`. Роутер прокидывает str(e)
    в UI.
  implication: Пользователь видит сырой текст telethon как toast. Нужен
    осмысленный структурный код + сообщение.

- timestamp: 2026-07-13 (field-test cycle)
  checked: DB senders — resolve "polina_ags"
  found: tg_username='polina_ags' → sender-8071536685
    (id db652110-3d1e-4a5c-9b29-fa69b8472f85, phone +16184955131 US,
    telegram_id 8071536685, restriction_status=spam_limited,
    twofa_password_enc IS NULL — we never persist the 2FA password, D-03).
  implication: точный аккаунт найден; наш стор пароля не хранит (transient).

- timestamp: 2026-07-13 (field-test cycle)
  checked: docker logs outreach-platform-api — timeline sender-8071536685
  found: три последовательных вызова:
    13:34:58 POST …/2fa/recovery-email → 400 (новый гейт NO_2FA_PASSWORD
      сработал — у аккаунта не было облачного пароля);
    13:36:25 POST …/2fa → 200 (пользователь ПОСТАВИЛ 2FA-пароль);
    13:36:31 POST …/2fa/recovery-email → 200 OK (повтор, «успех») —
      НО письмо не пришло.
  implication: гейт из прошлого цикла работает верно. Проблема на
    success-пути после установки пароля: 200 без ошибки, но письма нет.

- timestamp: 2026-07-13 (field-test cycle)
  checked: telethon 1.42.0 rpc dispatch для EMAIL_UNCONFIRMED_%d
    (rpc_message_to_error + rpcerrorlist)
  found: EMAIL_UNCONFIRMED_6 → EmailUnconfirmedError с code_length=6, и это
    ТОТ ЖЕ класс, что импортируется в коде (id совпал), isinstance=True.
  implication: гипотеза «code_length теряется» ОПРОВЕРГНУТА. Если бы
    EmailUnconfirmedError бросался — он бы ловился и code_length заполнялся.
    Значит на этом аккаунте он НЕ бросился → ветка «no exception».

- timestamp: 2026-07-13 (field-test cycle)  [ROOT CAUSE]
  checked: telethon TelegramClient.edit_2fa (reference impl) vs наш
    start_recovery_email UpdatePasswordSettingsRequest
  found: Reference edit_2fa при любой работе с email строит new_settings
    ПОЛНОСТЬЮ: `pwd.new_algo.salt1 += os.urandom(32)` ДО хеширования +
    new_algo=pwd.new_algo + new_password_hash=compute_digest(...) + hint +
    email + new_secure_settings=None. Наш код шлёт
    PasswordInputSettings(email=email) — ТОЛЬКО email, без new_algo,
    new_password_hash и без рандомизации соли.
  implication: Telegram принимает email-only UpdatePasswordSettings как
    no-op — НЕ запускает флоу подтверждения email, НЕ бросает
    EmailUnconfirmedError → наш код падает в ветку «No exception = no
    confirmation needed» → возвращает code_length: None → роутер всё равно
    отдаёт 200 EMAIL_CONFIRMATION_SENT → пользователь видит «успех», но
    письмо никогда не отправляется. ТОЧНОЕ совпадение с симптомом.

## Resolution

root_cause: ДВА бага, оба в recovery-email флоу.
  (1) [прошлый цикл] `start_recovery_email` безусловно вызывал `compute_check`
  → у аккаунта без 2FA `current_algo=None` → ValueError "unsupported password
  algorithm NoneType" → 500 toast. Закрыт гейтом NO_2FA_PASSWORD (см. ниже).
  (2) [ЭТОТ цикл — реальная причина "запрос прошёл, письмо не пришло"]
  `start_recovery_email` строил `UpdatePasswordSettingsRequest` с
  `new_settings=PasswordInputSettings(email=email)` — ТОЛЬКО email, без
  new_algo и new_password_hash. Telegram принимает такой запрос как no-op:
  НЕ запускает флоу подтверждения email, НЕ бросает EmailUnconfirmedError.
  Код падал в ветку "No exception = no confirmation needed" → возвращал
  code_length: None, а роутер всё равно отдавал 200 EMAIL_CONFIRMATION_SENT.
  Пользователь видел "успех", но письмо никогда не уходило. Эталонный
  telethon `edit_2fa` при работе с email ВСЕГДА рандомизирует
  `pwd.new_algo.salt1 += os.urandom(32)` и шлёт new_algo + new_password_hash
  (перехеш ТОГО ЖЕ пароля) + hint + email — только тогда Telegram шлёт код.
fix:
  (1) гейт `if not pwd.has_password: raise ValueError("NO_2FA_PASSWORD")` +
  роутер-маппинг NO_2FA_PASSWORD → 400 (без изменений с прошлого цикла).
  (2) `start_recovery_email` переписан по образцу telethon.edit_2fa:
  требует current_password (иначе ValueError CURRENT_PASSWORD_REQUIRED →
  400, чтобы случайно не снять 2FA перехешем пустого пароля);
  `pwd.new_algo.salt1 += os.urandom(32)`; `compute_digest(pwd.new_algo,
  current_password)`; шлёт PasswordInputSettings(new_algo, new_password_hash,
  hint=существующий, email, new_secure_settings=None). Ветка без
  EmailUnconfirmedError теперь возвращает already_confirmed=True, а роутер в
  этом случае отдаёт EMAIL_ALREADY_CONFIRMED (а не ложный
  EMAIL_CONFIRMATION_SENT), чтобы UI не просил несуществующий код.
verification: юнит-тесты в test_cr04_profile_call_signatures.py
  (NO_2FA_PASSWORD guard; CURRENT_PASSWORD_REQUIRED guard; captures
  UpdatePasswordSettings и проверяет new_algo+new_password_hash+email и
  code_length=6 из EmailUnconfirmedError) + router-тесты в
  test_account_profile.py (400 NO_2FA_PASSWORD, 200 EMAIL_CONFIRMATION_SENT,
  200 EMAIL_ALREADY_CONFIRMED). ЖДЁТ прод-верификации: пользователь
  повторяет флоу на аккаунте с установленным 2FA — письмо должно прийти.
files_changed:
  - app/services/telegram.py (full new_settings + salt randomize + require pw)
  - app/routers/senders.py (CURRENT_PASSWORD_REQUIRED map + EMAIL_ALREADY_CONFIRMED)
  - tests/test_cr04_profile_call_signatures.py (2 new regression tests)
  - tests/test_account_profile.py (EMAIL_ALREADY_CONFIRMED router test)
