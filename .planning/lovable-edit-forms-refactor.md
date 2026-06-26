# Refactor: Edit Forms — Campaign & Agent

## Context

Форма создания кампании (`campaigns/new`) — многошаговый визард с 7 шагами, визуальными карточками-пикерами и CSS-классами из дизайн-системы. Форма редактирования (`EditCampaignModal`) — 22+ полей в плоском списке с inline-стилями через локальные константы вместо классов. Нужно привести их к одному виду.

Агентская форма (`AgentEditor` в `agents.tsx`) уже сделана правильно — один компонент для создания и редактирования. Изменения там минимальны.

Все CSS-классы формы уже существуют в `src/styles/aimly.css`: `.field`, `.field__label`, `.field__hint`, `.input`, `.textarea`, `.select`, `.modal__scrim`, `.modal`, `.modal__head`, `.modal__body`, `.modal--wide`.

---

## Задача 1 — EditCampaignModal: заменить inline-CSS на классы

**Файл:** `src/components/EditCampaignModal.tsx`

### Что убрать

Удалить четыре константы в начале компонента `EditCampaignModal`:

```tsx
// УДАЛИТЬ всё это:
const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
  marginBottom: 4,
  display: "block",
};
const inputStyle: React.CSSProperties = {
  width: "100%",
  height: 34,
  padding: "0 10px",
  background: "var(--bg-soft)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 13,
  color: "var(--text)",
  outline: "none",
};
const taStyle: React.CSSProperties = {
  ...inputStyle,
  height: "auto",
  padding: 10,
  resize: "vertical",
  fontFamily: "inherit",
};
const fieldStyle: React.CSSProperties = { marginBottom: 14 };
```

### Что поставить вместо

Заменить паттерн использования:

```tsx
// ДО:
<div style={fieldStyle}>
  <label style={labelStyle}>Name</label>
  <input style={inputStyle} value={name} ... />
</div>

// ПОСЛЕ:
<div className="field">
  <label className="field__label">Name</label>
  <input className="input" value={name} ... />
</div>
```

```tsx
// ДО:
<textarea style={taStyle} rows={2} ... />

// ПОСЛЕ:
<textarea className="textarea" rows={2} ... />
```

```tsx
// ДО:
<select style={inputStyle} value={agentId} ... />

// ПОСЛЕ:
<select className="select" value={agentId} ... />
```

Применить ко всем полям в модале. Дополнительный `style={fieldStyle}` (marginBottom: 14) убрать — `.field` уже создаёт правильный gap между полями, а расстояние между секциями будет через `gap` в контейнере.

---

## Задача 2 — EditCampaignModal: структурировать поля по секциям

**Файл:** `src/components/EditCampaignModal.tsx`

### Структура модала

Обернуть поля в четыре секции. Каждая секция — `<div className="field">` не нужен на уровне секции, нужен заголовок и разделитель.

Паттерн секции:

```tsx
<div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
  {/* Разделитель между секциями */}
  <div style={{
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
    paddingTop: 20,
    borderTop: "1px solid var(--border)",
    marginTop: 6,
  }}>
    Заголовок секции
  </div>
  
  {/* Поля секции */}
  <div className="field">...</div>
  <div className="field">...</div>
</div>
```

### Разбивка полей по секциям

**Секция 1 — без заголовка (первая, идёт без borderTop):**
- Name
- Description / brief

**Секция 2 — "Agent & goal"** (с разделителем сверху):
- Agent (пока `<select>` — заменим в Задаче 4)
- Folder (пока `<select>` — оставить)
- Primary goal (пока `<select>` — заменим в Задаче 5)
- Кому пишем (`audience_hints`)

**Секция 3 — "AI behaviour"** (с разделителем):
- Ход разговора (`dialogue_flow` — StageEditor)
- Аргументы и факты (`arguments_facts`)
- Правила кампании (`campaign_rules`)

**Секция 4 — "Schedule"** (с разделителем):
- Message template
- Timezone + Start hour + End hour (в одну строку, `display: grid; gridTemplateColumns: 2fr 1fr 1fr; gap: 12`)
- Work days (кнопки дней)
- Re-contact toggle
- Recontact min age days (если `allowRecontact`)
- Start date + Stop date (в одну строку, `display: grid; gridTemplateColumns: 1fr 1fr; gap: 12`)

**Секция 5 — "Signals & webhooks"** (с разделителем):
- Сигнал «Лид» (`lead_trigger_hint`)
- Handoff trigger hint (`handoff_trigger_hint`)
- Finish trigger hint (`finish_trigger_hint`)
- Webhook URL (`webhook_url`)
- Lead webhook + Handoff webhook + Finish webhook (в одну строку, `display: grid; gridTemplateColumns: 1fr 1fr 1fr; gap: 12`)

### Внутри `.modal__body` использовать flex с gap:

```tsx
<div className="modal__body scroll" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
  {/* секции */}
</div>
```

---

## Задача 3 — EditCampaignModal: заменить wrapper на modal-классы

**Файл:** `src/components/EditCampaignModal.tsx`

### Сейчас

```tsx
return (
  <div
    role="dialog"
    aria-modal="true"
    aria-label="Edit campaign"
    onClick={onClose}
    style={{
      position: "fixed",
      inset: 0,
      background: "rgba(0,0,0,0.45)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 100,
      padding: 20,
    }}
  >
    <div
      className="card"
      onClick={(e) => e.stopPropagation()}
      style={{
        width: "100%",
        maxWidth: 720,
        maxHeight: "90vh",
        display: "flex",
        flexDirection: "column",
        background: "var(--bg)",
        padding: 0,
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Edit campaign</div>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            Update settings for {campaign.name}
          </div>
        </div>
        <button className="tb__icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      
      <div style={{ overflow: "auto", padding: 20 }}>
        {/* поля */}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 20px", borderTop: "1px solid var(--border)" }}>
        {/* кнопки */}
      </div>
    </div>
  </div>
);
```

### Должно стать

```tsx
return (
  <div className="modal__scrim" role="dialog" aria-modal="true" aria-label="Edit campaign" onClick={onClose}>
    <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
      
      <header className="modal__head">
        <div>
          <h3>Edit campaign</h3>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            {campaign.name}
          </div>
        </div>
        <button className="tb__icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </header>

      <div className="modal__body scroll" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {/* все секции с полями */}
        
        {error && (
          <div style={{ padding: 10, background: "var(--danger-soft, #fde2e1)", color: "var(--danger)", borderRadius: 8, fontSize: 13 }} role="alert">
            {error}
          </div>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 18px", borderTop: "1px solid var(--border)" }}>
        <button className="btn btn--ghost btn--sm" onClick={onClose}>Cancel</button>
        <button
          className="btn btn--primary btn--sm"
          onClick={() => { setError(null); saveMut.mutate(); }}
          disabled={saveMut.isPending || !name.trim()}
        >
          {saveMut.isPending ? "Saving…" : "Save changes"}
        </button>
      </div>

    </div>
  </div>
);
```

**Важно:** `modal--wide` даёт `max-width: 720px` (уже определён в aimly.css). `modal__body` уже имеет `overflow-y: auto` — класс `scroll` добавить для вертикального скролла если нужно.

---

## Задача 4 — EditCampaignModal: заменить agent `<select>` на карточки

**Файл:** `src/components/EditCampaignModal.tsx`

### Убрать

```tsx
<div style={fieldStyle}>
  <label style={labelStyle}>Agent</label>
  <select
    style={inputStyle}
    value={agentId ?? ""}
    onChange={(e) => setAgentId(e.target.value)}
  >
    {agents.map((a) => (
      <option key={a.id} value={a.id}>{a.name}</option>
    ))}
  </select>
</div>
```

### Поставить

Карточный пикер — точно такой же как в `AgentStep` в `campaigns.new.tsx`:

```tsx
<div className="field">
  <label className="field__label">Agent</label>
  {agents.length === 0 ? (
    <div className="muted text-sm" style={{ padding: 12 }}>No agents yet.</div>
  ) : (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
      {agents.map((a) => {
        const on = agentId === a.id;
        return (
          <button
            key={a.id}
            type="button"
            onClick={() => setAgentId(a.id)}
            style={{
              padding: 14,
              borderRadius: 11,
              textAlign: "left",
              border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
              background: on ? "var(--tg-blue-softer, var(--tg-blue-soft))" : "var(--bg)",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                className="avatar avatar--sm"
                style={{ background: "var(--tg-blue)", color: "white" }}
              >
                {(a.name || "?").slice(0, 1).toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>{a.name}</div>
                <div className="muted text-xs">{a.tone_preset || "Custom agent"}</div>
              </div>
              {on && <Check size={16} style={{ color: "var(--tg-blue)" }} />}
            </div>
            {a.system_prompt && (
              <div className="text-sm muted" style={{
                lineHeight: 1.45,
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}>
                {a.system_prompt}
              </div>
            )}
          </button>
        );
      })}
    </div>
  )}
</div>
```

Добавить `Check` в импорты из `lucide-react` если его там ещё нет.

---

## Задача 5 — EditCampaignModal: заменить primary_goal `<select>` на карточки

**Файл:** `src/components/EditCampaignModal.tsx`

### Убрать

```tsx
<div style={fieldStyle}>
  <label style={labelStyle}>Primary goal</label>
  <select style={inputStyle} value={primaryGoal} onChange={(e) => setPrimaryGoal(e.target.value)}>
    <option value="">—</option>
    <option value="book_meeting">Book meeting</option>
    <option value="qualify">Qualify</option>
    <option value="click">Click</option>
    <option value="engage">Engage</option>
  </select>
</div>
```

### Поставить

```tsx
<div className="field">
  <label className="field__label">Primary goal</label>
  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
    {GOAL_OPTIONS.map((g) => {
      const on = primaryGoal === g.id;
      const Icon = g.Icon;
      return (
        <button
          key={g.id}
          type="button"
          onClick={() => setPrimaryGoal(on ? "" : g.id)}
          style={{
            padding: "10px 12px",
            borderRadius: 10,
            textAlign: "left",
            display: "flex",
            gap: 10,
            alignItems: "center",
            border: `1.5px solid ${on ? "var(--tg-blue)" : "var(--border)"}`,
            background: on ? "var(--tg-blue-softer, var(--tg-blue-soft))" : "var(--bg)",
          }}
        >
          <div style={{
            width: 28, height: 28, borderRadius: 8,
            background: on ? "var(--tg-blue)" : "var(--bg-soft)",
            color: on ? "white" : "var(--text-muted)",
            display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
          }}>
            <Icon size={13} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{g.label}</div>
            <div className="muted text-xs">{g.desc}</div>
          </div>
        </button>
      );
    })}
  </div>
</div>
```

Добавить в начало файла (после импортов типов):

```tsx
import { Calendar, Flag, MousePointerClick, Smile } from "lucide-react";

const GOAL_OPTIONS = [
  { id: "book_meeting", label: "Book a meeting", desc: "Calendar invite confirmed", Icon: Calendar },
  { id: "qualify",      label: "Qualify the lead", desc: "Budget · timeline · authority", Icon: Flag },
  { id: "click",        label: "Get a click",       desc: "Visit link / sign up",          Icon: MousePointerClick },
  { id: "engage",       label: "Engage",             desc: "Warm 5+ msg conversation",     Icon: Smile },
] as const;
```

---

## Задача 6 — EditCampaignModal: улучшить folder `<select>`

**Файл:** `src/components/EditCampaignModal.tsx`

Минимальный фикс — добавить contact_count в текст опции, чтобы папки можно было различить:

```tsx
<div className="field">
  <label className="field__label">Folder</label>
  <select className="select" value={folderId ?? ""} onChange={(e) => setFolderId(e.target.value)}>
    <option value="">— Select folder —</option>
    {folders.map((f) => (
      <option key={f.id} value={f.id}>
        {f.name} · {f.contact_count.toLocaleString()} contacts
      </option>
    ))}
  </select>
</div>
```

---

## Задача 7 — Вынести StageEditor в общий компонент

**Новый файл:** `src/components/StageEditor.tsx`

Создать файл с компонентом на базе `StageEditor` из `campaigns.new.tsx`. Использовать CSS-классы:

```tsx
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";

interface DialogueStage {
  title: string;
  instruction: string;
}

export function StageEditor({
  stages,
  onChange,
}: {
  stages: DialogueStage[];
  onChange: (stages: DialogueStage[]) => void;
}) {
  const addStage = () => onChange([...stages, { title: "", instruction: "" }]);
  const removeStage = (idx: number) => onChange(stages.filter((_, i) => i !== idx));
  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...stages];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    onChange(next);
  };
  const moveDown = (idx: number) => {
    if (idx === stages.length - 1) return;
    const next = [...stages];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    onChange(next);
  };
  const updateStage = (idx: number, patch: Partial<DialogueStage>) =>
    onChange(stages.map((s, i) => (i === idx ? { ...s, ...patch } : s)));

  return (
    <div>
      {stages.length === 0 ? (
        <div style={{
          padding: "24px 18px",
          borderRadius: 10,
          border: "1.5px dashed var(--border)",
          background: "var(--bg-soft)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 8,
          textAlign: "center",
          marginBottom: 10,
        }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>Ход разговора пока пуст</div>
          <div className="muted text-sm" style={{ maxWidth: 360, lineHeight: 1.5 }}>
            Опишите 3–5 стадий разговора. Это задаёт сценарий именно этой кампании.
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 10 }}>
          {stages.map((stage, idx) => (
            <div
              key={idx}
              style={{
                padding: 14,
                borderRadius: 10,
                border: "1px solid var(--border)",
                background: "var(--bg-soft)",
                display: "flex",
                gap: 12,
                alignItems: "flex-start",
              }}
            >
              {/* Номер стадии */}
              <div style={{
                width: 24, height: 24, borderRadius: "50%",
                background: "var(--tg-blue)", color: "white",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0, fontSize: 11.5, fontWeight: 600, marginTop: 7,
              }}>
                {idx + 1}
              </div>

              {/* Поля */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
                <input
                  className="input"
                  placeholder={`Название стадии ${idx + 1} (необязательно)`}
                  value={stage.title}
                  onChange={(e) => updateStage(idx, { title: e.target.value })}
                />
                <textarea
                  className="textarea"
                  rows={2}
                  placeholder="Что должен делать ИИ на этой стадии?"
                  value={stage.instruction}
                  onChange={(e) => updateStage(idx, { instruction: e.target.value })}
                />
              </div>

              {/* Контролы порядка */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, flexShrink: 0 }}>
                <button
                  type="button"
                  className="tb__icon-btn"
                  aria-label="Переместить вверх"
                  disabled={idx === 0}
                  onClick={() => moveUp(idx)}
                  style={{ width: 28, height: 28, color: idx === 0 ? "var(--text-faint)" : "var(--tg-blue)" }}
                >
                  <ChevronUp size={14} />
                </button>
                <button
                  type="button"
                  className="tb__icon-btn"
                  aria-label="Переместить вниз"
                  disabled={idx === stages.length - 1}
                  onClick={() => moveDown(idx)}
                  style={{ width: 28, height: 28, color: idx === stages.length - 1 ? "var(--text-faint)" : "var(--tg-blue)" }}
                >
                  <ChevronDown size={14} />
                </button>
                <button
                  type="button"
                  className="tb__icon-btn"
                  aria-label="Удалить стадию"
                  onClick={() => removeStage(idx)}
                  style={{ width: 28, height: 28, color: "var(--danger)", marginTop: 4 }}
                >
                  <X size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={addStage}
          disabled={stages.length >= 7}
          style={{ color: "var(--tg-blue)" }}
        >
          <Plus size={13} /> Добавить стадию
        </button>
        {stages.length > 0 && (
          <span className="field__hint">{stages.length}/7 стадий</span>
        )}
      </div>
    </div>
  );
}
```

### Использовать в EditCampaignModal

```tsx
// Добавить импорт:
import { StageEditor } from "@/components/StageEditor";

// Убрать локальный InlineStageEditor целиком.
// Заменить использование:
<StageEditor stages={dialogueFlow} onChange={setDialogueFlow} />
```

### Использовать в campaigns.new.tsx

```tsx
// Добавить импорт:
import { StageEditor } from "@/components/StageEditor";

// Убрать локальный StageEditor целиком (функцию StageEditor из этого файла).
// Использование уже <StageEditor stages={dialogueFlow} onChange={setDialogueFlow} /> — ничего менять не нужно.
```

---

## Задача 8 — agents.tsx: заменить × на иконку X

**Файл:** `src/routes/_authenticated/agents.tsx`

В компоненте `AgentEditor`:

```tsx
// ДО:
<button className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Close">
  ×
</button>

// ПОСЛЕ:
<button className="tb__icon-btn" onClick={onClose} aria-label="Close">
  <X size={16} />
</button>
```

Убедиться что `X` импортирован из `lucide-react` (скорее всего уже есть в импортах).

---

## Задача 9 — Унификация языка лейблов в EditCampaignModal

**Файл:** `src/components/EditCampaignModal.tsx`

Привести все лейблы к одному языку (английский, т.к. продукт ориентирован на международный рынок):

| Было | Должно быть |
|------|-------------|
| `Кому пишем` | `Who are we writing to` |
| `Ход разговора` | `Conversation flow` |
| `Аргументы и факты` | `Arguments & facts` |
| `Правила кампании` | `Campaign rules` |
| `Сигнал «Лид»` | `Lead signal` |
| `Handoff trigger hint` | `Handoff signal` |
| `Finish trigger hint` | `Finish signal` |
| `Lead webhook` | `Lead webhook` ✓ |
| `Handoff webhook` | `Handoff webhook` ✓ |
| `Finish webhook` | `Finish webhook` ✓ |

Placeholder'ы можно оставить как есть.

---

## Итоговый порядок выполнения

1. **Задача 7** — создать `StageEditor.tsx` (нужен для остальных)
2. **Задачи 1+2** — убрать inline-CSS + добавить секции в EditCampaignModal
3. **Задача 3** — заменить wrapper на modal-классы
4. **Задачи 4+5** — заменить agent/goal select на карточки
5. **Задача 6** — folder select с contact_count
6. **Задача 8** — × → X icon в AgentEditor
7. **Задача 9** — унифицировать лейблы

---

## Что НЕ нужно менять

- Логику сохранения (PATCH с diff, `saveMut`) — не трогать
- Логику campaigns.new.tsx wizard (шаги, навигация, review) — не трогать
- AgentEditor логику — только иконка и импорт StageEditor если понадобится
- CSS-файлы — все нужные классы уже есть
- API-вызовы и типы — не трогать
