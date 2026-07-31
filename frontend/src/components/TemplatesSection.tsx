// Раздел «Шаблоны» (раздел 14 UI/UX-плана, функция 10 ТЗ): шаблоны ответов,
// дозапросов и WhatsApp (статус модерации Meta). Правка — руководитель/админ.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { TemplateRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const KIND_LABELS: Record<string, string> = {
  reply: "Ответы",
  followup: "Дозапросы",
  whatsapp: "WhatsApp",
};

const MODERATION_LABELS: Record<string, [string, string]> = {
  draft: ["черновик", "tone-neutral"],
  pending: ["на модерации Meta", "tone-info"],
  approved: ["одобрен", "tone-ok"],
  rejected: ["отклонён", "tone-warn"],
};

export default function TemplatesSection() {
  const { user } = useAuth();
  const canEdit = user?.role === "head" || user?.role === "admin";

  const [templates, setTemplates] = useState<TemplateRead[]>([]);
  const [selected, setSelected] = useState<TemplateRead | null>(null);
  const [draftBody, setDraftBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [newKind, setNewKind] = useState("reply");
  const [newName, setNewName] = useState("");
  const [newBody, setNewBody] = useState("");

  const load = async () => {
    try {
      const data = await api.listTemplates();
      setTemplates(data);
      setError(null);
      if (selected) {
        setSelected(data.find((t) => t.id === selected.id) ?? null);
      }
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const open = (t: TemplateRead) => {
    setSelected(t);
    setDraftBody(t.body);
  };

  const save = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.updateTemplate(selected.id, { body: draftBody });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    try {
      await api.createTemplate({
        kind: newKind,
        name: newName.trim(),
        body: newBody,
      });
      setCreateOpen(false);
      setNewName("");
      setNewBody("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const grouped = Object.entries(KIND_LABELS)
    .map(([kind, label]) => ({
      kind,
      label,
      items: templates.filter((t) => t.kind === kind),
    }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="requests-page">
      <div className="requests-header">
        <h1>Шаблоны</h1>
        {canEdit && (
          <button onClick={() => setCreateOpen((v) => !v)}>+ Новый шаблон</button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      {createOpen && (
        <div className="panel">
          <h2>Новый шаблон</h2>
          <div className="row">
            <div className="field">
              <label>Тип</label>
              <select value={newKind} onChange={(e) => setNewKind(e.target.value)}>
                <option value="reply">Ответ на типовой вопрос</option>
                <option value="followup">Дозапрос</option>
                <option value="whatsapp">WhatsApp (модерация Meta)</option>
              </select>
            </div>
            <div className="field" style={{ flex: 2 }}>
              <label>Название</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)} />
            </div>
          </div>
          <div className="field">
            <label>Текст (плейсхолдеры: {"{manager} {substance} {cas} {buyer}"})</label>
            <textarea
              rows={5}
              value={newBody}
              onChange={(e) => setNewBody(e.target.value)}
            />
          </div>
          <div className="actions">
            <button
              onClick={() => void create()}
              disabled={busy || !newName.trim() || !newBody.trim()}
            >
              Создать
            </button>
            <button className="secondary" onClick={() => setCreateOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="suppliers-layout">
        <div className="templates-list">
          {grouped.map((g) => (
            <div className="panel" key={g.kind}>
              <h2>{g.label}</h2>
              {g.items.map((t) => (
                <div
                  key={t.id}
                  className={`rfq-list-item ${selected?.id === t.id ? "row-active" : ""}`}
                  onClick={() => open(t)}
                >
                  <div>
                    {t.name} <span className="badge tone-neutral">v{t.version}</span>{" "}
                    {t.moderation && (
                      <span className={`badge ${MODERATION_LABELS[t.moderation][1]}`}>
                        {MODERATION_LABELS[t.moderation][0]}
                      </span>
                    )}
                  </div>
                  <div className="cas">
                    обновил: {t.updated_by ?? "—"}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>

        {selected && (
          <aside className="panel supplier-card">
            <h2>{selected.name}</h2>
            {canEdit ? (
              <>
                <textarea
                  className="template-editor"
                  rows={10}
                  value={draftBody}
                  onChange={(e) => setDraftBody(e.target.value)}
                />
                <div className="actions">
                  <button
                    onClick={() => void save()}
                    disabled={busy || draftBody === selected.body}
                  >
                    Сохранить (v{selected.version + 1})
                  </button>
                  {selected.kind === "whatsapp" && (
                    <button
                      className="secondary"
                      disabled={busy || selected.moderation === "pending"}
                      onClick={() =>
                        void api
                          .updateTemplate(selected.id, { moderation: "pending" })
                          .then(load)
                      }
                    >
                      Отправить на модерацию
                    </button>
                  )}
                </div>
                {selected.kind === "whatsapp" && (
                  <p className="note" style={{ marginTop: 8 }}>
                    Правка текста возвращает шаблон в «черновик» — потребуется
                    повторная модерация Meta.
                  </p>
                )}
              </>
            ) : (
              <pre className="letter">{selected.body}</pre>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
