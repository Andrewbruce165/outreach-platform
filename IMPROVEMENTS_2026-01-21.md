# IMPROVEMENTS_2026-01-21.md

## Дата: 21 января 2026 г.
## Автор: GitHub Copilot

## Обзор изменений
Исправлена критическая проблема с обработкой AI контекста в новых разговорах после их удаления через UI. Теперь бот корректно отвечает на сообщения после пересоздания диалогов.

## Проблема
После удаления разговора через веб-интерфейс и отправки нового сообщения от того же контакта:
- Создавался новый разговор без `ai_context_id`
- AI не отвечал на сообщения
- Сообщения появлялись в UI, но бот "замолкал"

## Корень проблемы
Функция `get_or_create_conversation` не сохраняла `ai_context_id` при создании новых разговоров, что приводило к потере контекста AI.

## Внесенные изменения

### 1. Обновление функции `get_or_create_conversation` (app/services/listener.py)
```python
# ДО:
async def get_or_create_conversation(
    self,
    session: AsyncSession,
    sender_id: str,
    contact_phone: str,
    contact_name: str,
    contact_telegram_id: int
) -> dict:

# ПОСЛЕ:
async def get_or_create_conversation(
    self,
    session: AsyncSession,
    sender_id: str,
    contact_phone: str,
    contact_name: str,
    contact_telegram_id: int,
    ai_context_id: Optional[str] = None
) -> dict:
```

### 2. Сохранение ai_context_id в новых разговорах
```sql
-- ДО:
INSERT INTO conversations (sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled)
VALUES (:sender_id, :phone, :name, :tg_id, true)

-- ПОСЛЕ:
INSERT INTO conversations (sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled, ai_context_id)
VALUES (:sender_id, :phone, :name, :tg_id, true, :ai_context_id)
```

### 3. Обновление ai_context_id в существующих разговорах
Добавлена логика обновления `ai_context_id` для существующих разговоров, если он отсутствует:
```python
# Если ai_context_id отсутствует в БД, но передан новый, обновляем
if not row[2] and ai_context_id:
    await session.execute(
        text("UPDATE conversations SET ai_context_id = :ai_context_id WHERE id = :id"),
        {"ai_context_id": ai_context_id, "id": str(row[0])}
    )
    await session.commit()
    conv_data["ai_context_id"] = ai_context_id
```

### 4. Обновление всех вызовов get_or_create_conversation
Все места вызова функции теперь передают `ai_context_id` из `sender_info`:

- `handle_incoming_message` (основная обработка входящих сообщений)
- Обработка документов/фото/видео
- `handle_outgoing_message` (обработка исходящих сообщений)

### 5. Исправление получения ai_context_id для AI буфера
```python
# ДО:
ai_context_id = conv["ai_context_id"]

# ПОСЛЕ:
ai_context_id = conv["ai_context_id"] or sender_info.get("ai_context_id")
```

## Тестирование
- ✅ Синтаксис кода проверен (`python3 -m py_compile`)
- ✅ Приложение запущено в Docker Compose
- ✅ AI отвечает на сообщения (проверено в логах)
- ✅ Удаление разговора через API работает
- ✅ Новый разговор создается с правильным `ai_context_id`

## Файлы, затронутые изменениями
- `app/services/listener.py` - основные изменения в логике создания/обновления разговоров

## Результат
Проблема решена. Теперь после удаления разговора и отправки нового сообщения:
- Создается новый разговор с корректным `ai_context_id`
- AI продолжает работать и отвечать на сообщения
- Бот не "замолкает" после пересоздания диалогов

## Следующие шаги
- Мониторить логи на предмет новых проблем
- Рассмотреть возможность автоматического тестирования данного сценария</content>
<parameter name="filePath">/root/apps/telegram-api/IMPROVEMENTS_2026-01-21.md