# Requirements: Outreach Platform

**Defined:** 2026-04-02
**Core Value:** Клиент подключил аккаунт и через 10 минут первое сообщение ушло — без программистов, без DevOps, без настройки серверов.

## v1 Requirements

### Multitenancy

- [ ] **TENT-01**: Все сущности (senders, contexts, contacts, queue, conversations) изолированы по workspace_id
- [ ] **TENT-02**: Workspace создаётся автоматически при регистрации
- [ ] **TENT-03**: Workspace имеет уникальный API-ключ для интеграций (n8n и др.)
- [ ] **TENT-04**: Запросы к API без валидного workspace-контекста отклоняются (403)

### Authentication

- [ ] **AUTH-01**: Пользователь вводит email → получает magic link на почту (Supabase Auth)
- [ ] **AUTH-02**: Переход по magic link создаёт JWT-сессию (Supabase)
- [ ] **AUTH-03**: FastAPI верифицирует Supabase JWT и извлекает workspace_id
- [ ] **AUTH-04**: Сессия сохраняется через browser refresh

### Account Onboarding

- [ ] **ONBD-01**: Пользователь добавляет Telegram-аккаунт через телефон + SMS-код
- [ ] **ONBD-02**: Поддерживается 2FA (пароль Telegram) при онбординге
- [ ] **ONBD-03**: Поддерживается QR-вход как альтернатива SMS
- [ ] **ONBD-04**: Добавленный аккаунт привязан к workspace пользователя
- [ ] **ONBD-05**: Пользователь видит список своих аккаунтов со статусом

### Contacts

- [ ] **CONT-01**: Пользователь загружает CSV с полями: телефон (обязательно), имя, компания, любые переменные
- [ ] **CONT-02**: Загруженные контакты привязаны к workspace
- [ ] **CONT-03**: Push-контакты через Workspace API (POST /api/v1/contacts)
- [ ] **CONT-04**: Переменные контакта ({{имя}}, {{компания}}) подставляются в текст сообщения

### Agent Settings

- [ ] **AGNT-01**: Каждый агент (Telegram-аккаунт) имеет свою страницу настроек
- [ ] **AGNT-02**: Пользователь задаёт rate limits агента: сообщений в минуту / час / день (с предупреждением при выходе за рекомендованные значения)
- [ ] **AGNT-03**: Пользователь задаёт расписание агента: рабочие часы и дни
- [ ] **AGNT-04**: Пользователь привязывает прокси к агенту (или выбирает из пула)
- [ ] **AGNT-05**: Пользователь привязывает AI-контекст к агенту
- [ ] **AGNT-06**: Страница агента показывает статус: активен / прогрев / пауза / ошибка

### AI Responder

- [ ] **AIRC-01**: Пользователь создаёт AI-контекст: промпт, тон, правила, FAQ
- [ ] **AIRC-02**: Пользователь задаёт auto_pause_triggers (фразы/паттерны для паузы AI)
- [ ] **AIRC-03**: Из inbox можно вручную переключить диалог в режим "менеджер" (AI отключается)
- [ ] **AIRC-04**: AI не отвечает системным ботам (SpamBot и др.)
- [ ] **AIRC-05**: AI-контекст привязан к workspace, применяется ко всем аккаунтам workspace

### Inbox

- [ ] **INBX-01**: Пользователь видит все входящие диалоги своего workspace
- [ ] **INBX-02**: В каждом диалоге видна история сообщений (исходящие + входящие)
- [ ] **INBX-03**: Виден статус AI: активен / пауза / режим менеджера
- [ ] **INBX-04**: Пользователь может переключить диалог в режим менеджера вручную

## v2 Requirements

### Analytics

- **ANLX-01**: Dashboard с метриками workspace: отправлено, доставлено, ответов, конверсия
- **ANLX-02**: История активности по каждому аккаунту
- **ANLX-03**: Экспорт статистики в CSV

### Advanced Outreach

- **ADVN-01**: Многошаговые последовательности (follow-up через N дней)
- **ADVN-02**: A/B тестирование текстов сообщений
- **ADVN-03**: Расписание отправки по временным зонам контакта

### Team

- **TEAM-01**: Несколько пользователей в одном workspace (роли: admin, member)
- **TEAM-02**: Приглашение по email

## Out of Scope

| Feature | Reason |
|---------|--------|
| Биллинг / платёжный шлюз | Отдельная интеграция после v1, не блокирует первого клиента |
| Мобильное приложение | Web-first |
| OAuth (Google/GitHub) | Email/password через Supabase достаточно для v1 |
| Real-time чат между операторами | Telegram inbox достаточен |
| Другие мессенджеры (WhatsApp, Instagram) | Платформа Telegram-специфична |
| Собственный AI (fine-tuning) | GPT-4o-mini достаточно для v1 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TENT-01 | Phase 1 | Pending |
| TENT-02 | Phase 1 | Pending |
| TENT-03 | Phase 1 | Pending |
| TENT-04 | Phase 1 | Pending |
| AUTH-01 | Phase 1 | Pending |
| AUTH-02 | Phase 1 | Pending |
| AUTH-03 | Phase 1 | Pending |
| AUTH-04 | Phase 1 | Pending |
| ONBD-01 | Phase 2 | Pending |
| ONBD-02 | Phase 2 | Pending |
| ONBD-03 | Phase 2 | Pending |
| ONBD-04 | Phase 2 | Pending |
| ONBD-05 | Phase 2 | Pending |
| CONT-01 | Phase 3 | Pending |
| CONT-02 | Phase 3 | Pending |
| CONT-03 | Phase 3 | Pending |
| CONT-04 | Phase 3 | Pending |
| AGNT-01 | Phase 2 | Pending |
| AGNT-02 | Phase 2 | Pending |
| AGNT-03 | Phase 2 | Pending |
| AGNT-04 | Phase 2 | Pending |
| AGNT-05 | Phase 2 | Pending |
| AGNT-06 | Phase 2 | Pending |
| AIRC-01 | Phase 4 | Pending |
| AIRC-02 | Phase 4 | Pending |
| AIRC-03 | Phase 4 | Pending |
| AIRC-04 | Phase 4 | Pending |
| AIRC-05 | Phase 4 | Pending |
| INBX-01 | Phase 4 | Pending |
| INBX-02 | Phase 4 | Pending |
| INBX-03 | Phase 4 | Pending |
| INBX-04 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 32 total
- Mapped to phases: 29
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-02*
*Last updated: 2026-04-02 after initial definition*
