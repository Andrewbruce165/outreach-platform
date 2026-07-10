---
slug: campaign-draft-save-validation-failed
status: resolved
trigger: "пробовал сохранить новую кампанию, но получил ошибку \"Draft not saved: Request validation failed\""
created: "2026-07-09"
updated: "2026-07-09"
---

# Debug Session: Campaign Draft Save — Request Validation Failed

## Symptoms

**Expected behavior:**
Кампания создаётся и появляется в списке кампаний.

**Actual behavior:**
При попытке сохранить новую кампанию UI показывает toast/сообщение "Draft not saved: Request validation failed". Черновик не сохраняется.

**Error messages:**
"Draft not saved: Request validation failed" — только текст тоста, без дополнительных деталей. Пользователь не смотрел Network tab / статус-код / тело ответа.

**Timeline:**
Раньше сохранение кампаний работало нормально, сломалось недавно (точная дата не известна).

**Reproduction:**
1. Открыть UI, начать создание новой кампании
2. Попытаться сохранить (черновик)
3. Наблюдать ошибку "Draft not saved: Request validation failed"

**Additional context:**
- Не уверены, происходит ли ошибка всегда или только с определёнными полями/шаблонами — нужно перепроверить.
- Похоже на несовпадение схемы между фронтом (Lovable, отдельный репо `aimly-tg-outreach`) и бэкендом (`app/routers/campaigns.py` или аналог) — см. CLAUDE.md примечание про расхождение Lovable-фронта со спекой (`SendMessageFromUIRequest` alias-кейс, `/telemetry/events` whitelist-кейс). Стоит проверить request body, который шлёт фронт при создании кампании, против Pydantic-схемы на бэке.

## Current Focus

- hypothesis: "Не payload-shape mismatch, а нарушение value-constraint: dialogue_flow[].instruction превышает backend-кап max_length=2000. Фронт не ограничивает длину textarea и не показывает какое поле упало → пользователь видит только 'Request validation failed'."
- test: "Прогрел логи api за 72ч → все 18 422-ответов на /campaigns имеют идентичный loc и type"
- expecting: "Если гипотеза верна — все ошибки будут string_too_long на dialogue_flow.instruction, а input.len > 2000"
- next_action: "DECIDED (product-fork): поднять кап instruction до 3000. Применено: (1) backend constr(max_length=3000); (2) фронт maxLength={3000}+char-counter в StageEditor; (3) фронт surface detail.errors[] (loc+msg) вместо голого 'Request validation failed'. CHECKPOINT: api-контейнер НЕ пересобран/не перезапущен — ждёт go-ahead."

## Evidence

- timestamp: 2026-07-09
  checked: "app/schemas/__init__.py CampaignCreate — есть ли extra='forbid'"
  found: "model_config = ConfigDict(from_attributes=True). extra не forbid → лишние поля игнорируются, не дают 422"
  implication: "Классический payload-shape mismatch (лишнее/переименованное поле) ИСКЛЮЧЁН как причина. Причина — конкретное значение поля."

- timestamp: 2026-07-09
  checked: "Recent commit 60f3d3b (frontend) + 52c93ec (backend) — удаление max_new_dialogs_per_day"
  found: "Поле удалено синхронно на обеих сторонах; фронт больше не шлёт его. И даже если бы слал — было бы проигнорировано."
  implication: "Недавнее удаление dialog-cap НЕ причина 422."

- timestamp: 2026-07-09
  checked: "DialogueStage + PrimaryGoal схемы фронт vs бэк"
  found: "Совпадают (title?/instruction; 4 goal-литерала идентичны). Shape корректен."
  implication: "Структурный mismatch исключён."

- timestamp: 2026-07-09
  checked: "docker compose logs api --since 72h → все Validation error на POST/PATCH /campaigns"
  found: "18/18 ошибок идентичны: {'type':'string_too_long', 'loc':('body','dialogue_flow',0,'instruction'), 'msg':'String should have at most 2000 characters'}. Длины input: 2323 и 2766 символов."
  implication: "Корневая причина установлена однозначно: instruction диалог-стейджа превышает 2000-символьный кап."

- timestamp: 2026-07-09
  checked: "git log -L на строке instruction: constr(max_length=2000)"
  found: "Кап max_length=2000 добавлен в Phase 11 (commit 2bf0c89 feat(11-02)) и НИКОГДА не менялся."
  implication: "'Сломалось недавно' — восприятие пользователя: он недавно начал писать более длинные детальные инструкции стадий (2323/2766 симв.), которые перевалили за давно существующий кап. Раньше короткие инструкции проходили."

- timestamp: 2026-07-09
  checked: "app/main.py validation_exception_handler + frontend src/lib/api.ts errMsg"
  found: "Backend 422 возвращает detail.errors[] с точным полем/причиной, НО top-level detail.message = 'Request validation failed'. Фронтовый errMsg/ApiError показывает только detail.message, detail.errors[] игнорируется."
  implication: "Непрозрачность тоста — вторая часть бага: даже когда backend точно говорит какое поле упало, фронт это не показывает. Фикс UX (surface detail.errors) полезен независимо от решения по капу."

## Eliminated

- hypothesis: "Payload-shape mismatch — фронт шлёт лишнее/переименованное/пропущенное поле (как SendMessageFromUIRequest / telemetry whitelist)"
  evidence: "CampaignCreate не имеет extra='forbid' → лишние поля игнорируются. DialogueStage/PrimaryGoal/tools shape совпадает. max_new_dialogs_per_day удалён синхронно."
  timestamp: 2026-07-09

## Resolution

- root_cause: "Backend DialogueStage.instruction = constr(min_length=1, max_length=2000) (T2 size-guard, Phase 11, commit 2bf0c89, никогда не менялся). Фронтовый редактор стадий диалога (StageEditor, используется в campaigns.new.tsx и EditCampaignModal.tsx) НЕ ограничивал длину textarea и не валидировал её перед отправкой. Когда пользователь писал инструкцию стадии > 2000 символов (в логах 2323 и 2766), backend отвечал 422 string_too_long на dialogue_flow[i].instruction, а фронт показывал единый непрозрачный тост 'Draft not saved: Request validation failed' без указания поля/причины (detail.errors[] от backend игнорировался, брался только detail.message)."
  fix: |
    Product-fork решение: поднять кап (не резать длинные инструкции) + сделать ошибку прозрачной.
    1. Backend (`app/schemas/__init__.py::DialogueStage.instruction`): constr(min_length=1, max_length=2000) → max_length=3000. Docstring обновлён с датой/причиной изменения.
    2. Frontend (`src/components/StageEditor.tsx`, общий компонент для campaigns.new.tsx и EditCampaignModal.tsx): добавлен `maxLength={3000}` на textarea instruction + live character-counter (X/3000, красный при достижении лимита) — пользователь больше не может физически ввести > 3000 символов и видит остаток до лимита заранее.
    3. Frontend (`src/lib/error-codes.ts`): добавлен маппинг `VALIDATION_ERROR` → `formatValidationErrors()`, который читает `detail.errors[]` (loc+msg, до 3 штук + счётчик "+N more") и формирует конкретное сообщение вместо общего "Request validation failed". Уже был подключён через `errorMessageFromEnvelope()` в `src/lib/api.ts` (существующий путь ошибок), никаких доп. правок в api.ts не потребовалось — там уже прокидывался весь `detail` объект в `errorMessageFromEnvelope(code, detail)`, только маппинга для кода VALIDATION_ERROR не было.
    4. Test drift: `tests/test_phase5_1_agents_v2.py` содержал жёсткий тест на старую границу (`instruction="x" * 2001` должен был вызывать ValidationError) — обновлён на `"x" * 3001`, иначе тест стал бы ложно-красным после поднятия капа.
  verification: |
    - Targeted pytest через test-overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_1_agents_v2.py tests/test_phase5_1_campaign_v2.py tests/test_phase5_1_campaign_v2_router.py -q`): 27 passed, включая обновлённую границу 3000/3001.
    - `npx tsc --noEmit` на фронт-репо: только pre-existing несвязанные ошибки типов в других файлах (routeTree/campaigns.$id.tsx) — ни StageEditor.tsx, ни error-codes.ts не дают новых ошибок.
    - Пересобран и перезапущен outreach-platform-api (`docker compose up -d --build api`, 2026-07-09 08:37 UTC) — контейнер стартовал штатно, все воркеры и миграции (59 applied) в норме.
    - Живая проверка внутри running-контейнера: `DialogueStage(instruction='x'*2500)` и `*3000` → accepted; `*3001` → ValidationError (rejected). Подтверждает, что реальные пользовательские инструкции (2323/2766 символов) теперь проходят, а новый кап 3000 действительно применён в живом коде.
  files_changed:
    - "app/schemas/__init__.py (backend repo, working tree — не закоммичено, применено live через rebuild)"
    - "tests/test_phase5_1_agents_v2.py (backend repo, working tree — не закоммичено)"
    - "/root/apps/aimly/aimly-tg-outreach/src/components/StageEditor.tsx (frontend repo, закоммичено 1070b30)"
    - "/root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts (frontend repo, закоммичено 1070b30)"
