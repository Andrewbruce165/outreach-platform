import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";

export interface DialogueStage {
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
        <div
          style={{
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
          }}
        >
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
              <div
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  background: "var(--tg-blue)",
                  color: "white",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  fontSize: 11.5,
                  fontWeight: 600,
                  marginTop: 7,
                }}
              >
                {idx + 1}
              </div>

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
