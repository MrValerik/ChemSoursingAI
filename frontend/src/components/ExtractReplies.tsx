import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ExtractedQuote, QuotationRead } from "../api/types";

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
  const [text, setText] = useState(SAMPLE);
  const [useLlm, setUseLlm] = useState(false);
  const [preview, setPreview] = useState<ExtractedQuote | null>(null);
  const [quotes, setQuotes] = useState<QuotationRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    run(async () => setPreview(await api.extractQuote(text, useLlm)));

  const onStore = () =>
    run(async () => {
      await api.extractAndStore(rfqId, text, useLlm);
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
          Использовать LLM (иначе только правила)
        </label>
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
