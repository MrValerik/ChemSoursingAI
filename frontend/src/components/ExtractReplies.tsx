import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ExtractedQuote, QuotationRead } from "../api/types";
import type { PromptRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

interface Props {
  rfqId: number;
  onStored: () => void;
}

const SAMPLE =
  "For Acetylsalicylic acid (CAS 50-78-2), USP grade, our best price is " +
  "USD 12.50/kg CIP Moscow. MOQ 25 kg. We can provide CoA and TDS. " +
  "Payment T/T in advance. Lead time 15 days.";

function confClass(conf: number): string {
  if (conf >= 0.85) return "ok";
  if (conf >= 0.7) return "muted";
  return "err";
}

function Field({
  label,
  value,
  conf,
}: {
  label: string;
  value: string | number | boolean | null;
  conf?: number;
}) {
  const shown =
    value === null || value === ""
      ? "—"
      : typeof value === "boolean"
        ? value
          ? "да"
          : "нет"
        : String(value);
  return (
    <div className="quote-field">
      <span className="quote-label">{label}</span>
      <span className="quote-value">{shown}</span>
      {conf !== undefined && (
        <span className={`badge ${confClass(conf)}`}>{Math.round(conf * 100)}%</span>
      )}
    </div>
  );
}

export default function ExtractReplies({ rfqId, onStored }: Props) {
  const { user } = useAuth();
  const [text, setText] = useState(SAMPLE);
  // Qwen — основной путь; backend сам переключится на правила, если модель недоступна.
  const [useLlm, setUseLlm] = useState(true);
  const [preview, setPreview] = useState<ExtractedQuote | null>(null);
  const [quotes, setQuotes] = useState<QuotationRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [prompts, setPrompts] = useState<PromptRead[]>([]);
  const [promptId, setPromptId] = useState<number | null>(null);
  const [instructions, setInstructions] = useState("");

  const loadQuotes = async () => {
    try {
      setQuotes(await api.listQuotations(rfqId));
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    setPreview(null);
    void loadQuotes();
    void Promise.all([api.listPrompts(), api.getRfqAiSettings(rfqId)])
      .then(([allPrompts, setting]) => {
        setPrompts(allPrompts.filter((p) => p.kind === "extraction" && p.is_active));
        setPromptId(setting.prompt_template_id);
        setInstructions(setting.additional_instructions);
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onExtract = () =>
    run(async () => {
      if (user?.role !== "auditor") {
        await api.saveRfqAiSettings(rfqId, {
          prompt_template_id: promptId,
          additional_instructions: instructions,
        });
      }
      setPreview(await api.extractQuote(text, useLlm, rfqId, instructions));
    });

  const onStore = () =>
    run(async () => {
      if (user?.role !== "auditor") {
        await api.saveRfqAiSettings(rfqId, {
          prompt_template_id: promptId,
          additional_instructions: instructions,
        });
      }
      await api.extractAndStore(rfqId, text, useLlm, instructions);
      setPreview(null);
      await loadQuotes();
      onStored();
    });

  const conf = preview?.field_confidence ?? {};

  return (
    <div className="panel">
      <h2>Ответ поставщика → котировка</h2>

      <div className="field">
        <label>Текст ответа (email / сообщение)</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{ minHeight: 96 }}
        />
      </div>

      <div className="checks" style={{ marginBottom: 8 }}>
        <label>
          <input
            type="checkbox"
            checked={useLlm}
            onChange={(e) => setUseLlm(e.target.checked)}
          />
          Использовать Qwen 27B (при недоступности автоматически применятся правила)
        </label>
      </div>

      <div className="row">
        <div className="field">
          <label>Промпт извлечения</label>
          <select
            value={promptId ?? ""}
            disabled={user?.role === "auditor"}
            onChange={(e) => setPromptId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Системный по умолчанию</option>
            {prompts.map((p) => (
              <option key={p.id} value={p.id}>{p.name} · v{p.version}</option>
            ))}
          </select>
        </div>
        <div className="field" style={{ flex: 2 }}>
          <label>Дополнительные инструкции для ИИ</label>
          <textarea
            rows={3}
            maxLength={4000}
            disabled={user?.role === "auditor"}
            placeholder="Например: учитывать только фармацевтический грейд и наличие GMP"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>
      </div>

      <div className="actions">
        <button className="secondary" onClick={onExtract} disabled={busy || !text}>
          Извлечь (предпросмотр)
        </button>
        <button onClick={onStore} disabled={busy || !text}>
          Сохранить котировку
        </button>
      </div>

      {error && <p className="error">Ошибка: {error}</p>}

      {preview && (
        <div className="quote-grid" style={{ marginTop: 12 }}>
          <Field label="Цена" value={preview.price} conf={conf.price} />
          <Field label="Валюта" value={preview.currency} conf={conf.currency} />
          <Field label="Базис" value={preview.incoterm} conf={conf.incoterm} />
          <Field label="MOQ" value={preview.moq} conf={conf.moq} />
          <Field label="Грейд" value={preview.grade} conf={conf.grade} />
          <Field label="Оплата" value={preview.payment_terms} conf={conf.payment_terms} />
          <Field label="Срок" value={preview.lead_time} conf={conf.lead_time} />
          <Field label="CoA" value={preview.has_coa} conf={conf.has_coa} />
          <Field label="TDS" value={preview.has_tds} conf={conf.has_tds} />
          <div className="quote-field">
            <span className="quote-label">Источник</span>
            <span className="quote-value">{preview.method}</span>
          </div>
        </div>
      )}

      {quotes.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="note">Сохранённые котировки: {quotes.length}</div>
          {quotes.map((q) => (
            <div key={q.id} className="rfq-list-item" style={{ cursor: "default" }}>
              <div>
                {q.price ?? "—"} {q.currency ?? ""} · {q.incoterm ?? "—"} · MOQ{" "}
                {q.moq ?? "—"}{" "}
                <span className={`badge ${q.is_complete ? "ok" : "err"}`}>
                  {q.is_complete ? "полная" : "неполная"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
