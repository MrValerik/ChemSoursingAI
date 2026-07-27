import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { PromptKind, PromptRead, PromptVersionRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const KIND_LABELS: Record<PromptKind, string> = {
  extraction: "Извлечение котировки",
  rfq_generation: "Формирование первого письма",
  substance_identity: "Идентификация вещества",
  supplier_search: "Поиск поставщиков",
  qualification: "Квалификация",
  followup: "Дозапрос данных",
};

const SAMPLE =
  "CAS 50-78-2, ацетилсалициловая кислота, чистота 99,5%, " +
  "фармацевтический грейд. Найди производителей в Китае и запроси GMP.";

export default function PromptStudio() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin" || user?.role === "head";
  const [items, setItems] = useState<PromptRead[]>([]);
  const [selected, setSelected] = useState<PromptRead | null>(null);
  const [versions, setVersions] = useState<PromptVersionRead[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [sample, setSample] = useState(SAMPLE);
  const [extra, setExtra] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newKind, setNewKind] = useState<PromptKind>("supplier_search");

  const load = async () => {
    const data = await api.listPrompts();
    setItems(data);
    if (!selected && data.length) open(data[0]);
    if (selected) {
      const fresh = data.find((p) => p.id === selected.id) ?? null;
      if (fresh) open(fresh);
    }
  };

  const open = (prompt: PromptRead) => {
    setSelected(prompt);
    setName(prompt.name);
    setDescription(prompt.description ?? "");
    setSystemPrompt(prompt.system_prompt);
    setOutput("");
    void api.promptVersions(prompt.id).then(setVersions).catch(() => setVersions([]));
  };

  useEffect(() => {
    void load().catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.updatePrompt(selected.id, {
        name: name.trim(),
        description: description.trim() || null,
        system_prompt: systemPrompt,
      });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setOutput("");
    try {
      const result = await api.previewPrompt(selected.id, sample, extra || undefined);
      setOutput(result.output);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createPrompt({
        kind: newKind,
        name: `Новый промпт: ${KIND_LABELS[newKind]}`,
        description: "Настройте назначение промпта",
        system_prompt:
          "Проанализируй задачу по закупке, используя только проверяемые факты. " +
          "Отвечай по-русски и указывай ссылку на источник для каждого вывода.",
      });
      await load();
      open(created);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="requests-page">
      <div className="requests-header">
        <div>
          <h1>ИИ-промпты</h1>
          <p className="note">
            Все промпты можно писать по-русски. Системные правила редактируют
            руководитель и администратор, версии сохраняются автоматически.
          </p>
        </div>
        {canEdit && (
          <div className="actions">
            <select value={newKind} onChange={(e) => setNewKind(e.target.value as PromptKind)}>
              {Object.entries(KIND_LABELS).map(([kind, label]) => (
                <option key={kind} value={kind}>{label}</option>
              ))}
            </select>
            <button onClick={() => void create()}>+ Новый промпт</button>
          </div>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      <div className="suppliers-layout">
        <div className="templates-list">
          {items.map((prompt) => (
            <div
              key={prompt.id}
              className={`panel rfq-list-item ${
                selected?.id === prompt.id ? "row-active" : ""
              }`}
              onClick={() => open(prompt)}
            >
              <div>
                {prompt.name}{" "}
                <span className="badge tone-neutral">v{prompt.version}</span>
                {!prompt.is_active && <span className="badge tone-warn">выключен</span>}
              </div>
              <div className="cas">{KIND_LABELS[prompt.kind]}</div>
            </div>
          ))}
        </div>

        {selected && (
          <div>
            <div className="panel">
              <h2>{KIND_LABELS[selected.kind]}</h2>
              <div className="field">
                <label>Название</label>
                <input value={name} disabled={!canEdit} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="field">
                <label>Назначение</label>
                <input
                  value={description}
                  disabled={!canEdit}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Системный промпт</label>
                <textarea
                  rows={9}
                  value={systemPrompt}
                  disabled={!canEdit}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                />
              </div>
              {canEdit && (
                <div className="actions">
                  <button disabled={busy || systemPrompt.trim().length < 20} onClick={() => void save()}>
                    Сохранить новую версию
                  </button>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      void api
                        .updatePrompt(selected.id, { is_active: !selected.is_active })
                        .then(load)
                        .catch((e) => setError(String(e)))
                    }
                  >
                    {selected.is_active ? "Отключить" : "Включить"}
                  </button>
                </div>
              )}
              <p className="note">
                Защита от инструкций внутри писем и обязательная проверка фактов
                добавляются backend и не могут быть отключены этим текстом.
              </p>
            </div>

            <div className="panel">
              <h2>Предпросмотр на Qwen</h2>
              <div className="field">
                <label>Тестовые входные данные</label>
                <textarea rows={5} value={sample} onChange={(e) => setSample(e.target.value)} />
              </div>
              <div className="field">
                <label>Дополнительные инструкции пользователя</label>
                <textarea rows={3} value={extra} onChange={(e) => setExtra(e.target.value)} />
              </div>
              <button disabled={busy || !sample.trim()} onClick={() => void preview()}>
                {busy ? "Qwen обрабатывает…" : "Запустить предпросмотр"}
              </button>
              {output && <pre className="letter" style={{ marginTop: 12 }}>{output}</pre>}
            </div>

            <div className="panel">
              <h2>История версий</h2>
              {versions.map((version) => (
                <div className="rfq-list-item" key={version.id}>
                  <span>v{version.version}</span>
                  <span className="note">
                    {version.changed_by ?? "—"} ·{" "}
                    {new Date(version.created_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
