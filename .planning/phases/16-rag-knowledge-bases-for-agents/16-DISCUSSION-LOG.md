# Phase 16: RAG Knowledge Bases for Agents - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-30
**Phase:** 16-rag-knowledge-bases-for-agents
**Areas discussed:** Источники данных KB, Retrieval (когда агент идёт в KB), Хранилище векторов, Привязка KB↔агент, Судьба статического поля knowledge_base, Модель эмбеддингов, UX статуса индексации

---

## Источники данных KB (ingest)

| Option | Description | Selected |
|--------|-------------|----------|
| Текст + файлы | Вставка текста + загрузка PDF/DOCX/TXT/MD/CSV | ✓ |
| Только текст/Q&A | Минимум: paste текста/Q&A без парсинга файлов | |
| Текст + файлы + URL | Плюс краулинг ссылок/сайтов | |

**User's choice:** Текст + файлы
**Notes:** URL-краулинг отложен из v1 (Deferred).

---

## Retrieval — когда агент обращается к KB

| Option | Description | Selected |
|--------|-------------|----------|
| Авто + порог | Retrieval на каждое входящее, инжект только релевантных чанков по порогу | |
| Tool-call | Агент сам вызывает search_knowledge_base по необходимости | ✓ |
| Всегда топ-K | Безусловный инжект топ-K на каждый ответ | |

**User's choice:** Tool-call (`search_knowledge_base`)
**Notes:** Дословно совпадает с формулировкой пользователя «ходит в неё по необходимости». Тул доступен только если у агента есть подключённые KB; ищет по всем подключённым.

---

## Хранилище векторов / индекс

| Option | Description | Selected |
|--------|-------------|----------|
| pgvector | Расширение в текущем PostgreSQL 16 | ✓ |
| Внешний (Qdrant) | Отдельный вектор-сервис | |
| Keyword (FTS) | Postgres full-text без эмбеддингов | |

**User's choice:** pgvector
**Notes:** Та же БД → workspace-изоляция и бэкапы бесплатно, не плодим инфру. Образ db → `pgvector/pgvector:pg16`.

---

## Привязка KB ↔ агент

| Option | Description | Selected |
|--------|-------------|----------|
| Агент M:N, поиск по всем | Только агент, несколько KB, retrieval по всем подключённым | ✓ |
| Агент + кампания | Кампания может переопределить набор KB | |
| Агент 1:N | Один KB на агента | |

**User's choice:** Агент M:N, поиск по всем подключённым
**Notes:** Ровно как описал пользователь. Уровень кампании отложен (Deferred).

---

## Судьба статического поля knowledge_base (Phase 11)

| Option | Description | Selected |
|--------|-------------|----------|
| Оставить рядом | Статический блок остаётся, RAG KB — отдельно | ✓ |
| Заменить на RAG | Мигрировать в KB, убрать колонку | |
| Скрыть из UI | Колонку не трогать, поле убрать из формы | |

**User's choice:** Оставить рядом
**Notes:** Статика заполняет слот [БАЗА ЗНАНИЙ] в промпте; RAG приходит через tool-result. Существующих агентов не ломаем.

---

## Модель эмбеддингов

| Option | Description | Selected |
|--------|-------------|----------|
| OpenAI 3-small | text-embedding-3-small (1536) | ✓ |
| OpenAI 3-large | text-embedding-3-large (3072) | |
| Ты решишь | На усмотрение research/plan | |

**User's choice:** OpenAI text-embedding-3-small (1536 dim)
**Notes:** OpenAI уже подключён, дёшево. Имя модели — в env-knob, дефолт 3-small.

---

## UX статуса индексации

| Option | Description | Selected |
|--------|-------------|----------|
| На документ | Статус + кол-во чанков на каждый источник | |
| На всю KB | Один статус на базу | |
| Ты решишь | На усмотрение планировщика/UI-фазы | ✓ |

**User's choice:** Ты решишь → **уточнено пользователем после обсуждения (2026-06-30) с приложенным примером UI.**
**Notes:** Изначально оставлено на усмотрение, затем пользователь прислал референс KB detail-вида и закрепил **оба уровня**: (1) данные по каждому документу (таб Documents), (2) агрегат по всей KB (счётчики DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE + Status/Type/Updated). Табы вида: Documents · Search · Agents · Settings. Зафиксировано в CONTEXT как D-09/D-10/D-11.

## Claude's Discretion

- UX статуса индексации (рекомендация: per-document статус + chunk count).
- Чанкинг (размер/overlap), top-K и порог в search_knowledge_base, формат tool-result, тип pgvector-индекса (HNSW/IVFFlat), обработка пустого результата, выбор библиотек парсинга PDF/DOCX.

## Deferred Ideas

- URL/сайт-краулинг как источник KB.
- KB на уровне кампании.
- Замена/миграция статического knowledge_base на RAG.
- Аналитика использования KB.
- Шаринг KB между workspace.
