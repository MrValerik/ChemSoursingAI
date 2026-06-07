// Карточка запроса (раздел 7 UI/UX-плана): слева параметры и статус-конвейер,
// справа вкладки полного пути обработки. Кнопка «Передать в ручной разбор»
// доступна на любом шаге.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PriceHistoryItem, RFQRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ExtractReplies from "./ExtractReplies";
import Summary from "./Summary";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";

type TabKey =
  | "verification"
  | "rfq"
  | "suppliers"
  | "dispatch"
  | "replies"
  | "summary"
  | "history";

const TABS: { key: TabKey; label: string }[] = [
  { key: "verification", label: "Верификация" },
  { key: "rfq", label: "RFQ" },
  { key: "suppliers", label: "Поставщики" },
  { key: "dispatch", label: "Рассылка" },
  { key: "replies", label: "Ответы" },
  { key: "summary", label: "Сводка" },
  { key: "history", label: "История" },
];

// Статус → пройденные этапы конвейера (раздел 7: «Этапы»).
const STAGES = ["Проверка", "RFQ", "Рассылка", "Ответы", "Сводка"];
const STAGE_BY_STATUS: Record<string, number> = {
  draft: 0,
  verified: 1,
  sent: 3,
  collecting: 3,
  parsed: 4,
  summarized: 5,
  escalated: 4,
  closed: 5,
};

const ESCALATION_REASONS: [string, string][] = [
  ["other", "Другое"],
  ["grade", "Нестандартный грейд"],
  ["logistics", "Опасная логистика"],
  ["shortage", "Дефицит"],
  ["custom_synthesis", "Кастомный синтез"],
];

export default function RfqDetail({
  rfq,
  onBack,
  onChanged,
}: {
  rfq: RFQRead;
  onBack: () => void;
  onChanged: (r: RFQRead) => void;
}) {
  const { user } = useAuth();
  const [tab, setTab] = useState<TabKey>("verification");
  const [refreshKey, setRefreshKey] = useState(0);

  const [escOpen, setEscOpen] = useState(false);
  const [escReason, setEscReason] = useState("other");
  const [escNote, setEscNote] = useState("");
  const [escBusy, setEscBusy] = useState(false);
  const [escError, setEscError] = useState<string | null>(null);

  const canEscalate = user?.role !== "auditor" && rfq.status !== "escalated";
  const doneStages = STAGE_BY_STATUS[rfq.status] ?? 0;

  const escalate = async () => {
    setEscBusy(true);
    setEscError(null);
    try {
      await api.escalateRfq(rfq.id, escReason, escNote.trim() || null);
      setEscOpen(false);
      setEscNote("");
      onChanged(await api.getRfq(rfq.id));
    } catch (e) {
      setEscError(e instanceof Error ? e.message : String(e));
    } finally {
      setEscBusy(false);
    }
  };

  return (
    <div className="requests-page">
      <div className="detail-header">
        <button className="secondary back-btn" onClick={onBack}>
          ← К запросам
        </button>
        <h1>
          RFQ #{rfq.id} · {rfq.name}
        </h1>
        <span className={`badge tone-${STATUS_TONE[rfq.status]}`}>
          {STATUS_LABELS[rfq.status]}
        </span>
        {canEscalate && (
          <button
            className="secondary escalate-btn"
            onClick={() => setEscOpen((v) => !v)}
          >
            Передать в ручной разбор
          </button>
        )}
      </div>

      {escOpen && (
        <div className="panel escalate-form">
          <h2>Передача в ручной разбор</h2>
          <div className="row">
            <div className="field">
              <label>Причина</label>
              <select value={escReason} onChange={(e) => setEscReason(e.target.value)}>
                {ESCALATION_REASONS.map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: 2 }}>
              <label>Комментарий</label>
              <input
                value={escNote}
                onChange={(e) => setEscNote(e.target.value)}
                placeholder="Что требует внимания специалиста"
              />
            </div>
          </div>
          {escError && <p className="error">{escError}</p>}
          <div className="actions">
            <button onClick={() => void escalate()} disabled={escBusy}>
              {escBusy ? "Передача…" : "Передать"}
            </button>
            <button className="secondary" onClick={() => setEscOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="detail-layout">
        <aside className="detail-params panel">
          <h2>Параметры</h2>
          <dl className="params-list">
            <dt>CAS</dt>
            <dd>
              {rfq.cas}{" "}
              {rfq.verified ? (
                <span className="badge tone-ok">проверен</span>
              ) : (
                <span className="badge tone-neutral">не проверен</span>
              )}
            </dd>
            {rfq.purity && (
              <>
                <dt>Чистота</dt>
                <dd>{rfq.purity}</dd>
              </>
            )}
            {rfq.volume && (
              <>
                <dt>Объём</dt>
                <dd>{rfq.volume}</dd>
              </>
            )}
            {rfq.target_price != null && (
              <>
                <dt>Ориентир</dt>
                <dd>
                  {rfq.target_price} {rfq.currency}
                </dd>
              </>
            )}
            <dt>Базисы</dt>
            <dd>{(rfq.incoterms ?? []).join(", ") || "—"}</dd>
            <dt>Каналы</dt>
            <dd>{(rfq.channels ?? []).join(", ") || "—"}</dd>
            {rfq.owner_name && (
              <>
                <dt>Ответственный</dt>
                <dd>{rfq.owner_name}</dd>
              </>
            )}
          </dl>

          <h2 style={{ marginTop: 16 }}>Этапы</h2>
          <ol className="stages">
            {STAGES.map((s, i) => (
              <li
                key={s}
                className={i < doneStages ? "done" : i === doneStages ? "current" : ""}
              >
                {s}
              </li>
            ))}
          </ol>
          {rfq.status === "escalated" && (
            <p className="note esc-note">Запрос передан в ручной разбор.</p>
          )}
        </aside>

        <div className="detail-main">
          <div className="tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`tab ${tab === t.key ? "active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "verification" && <VerificationTab rfq={rfq} />}

          {tab === "rfq" && (
            <div className="panel">
              <h2>Текст запроса</h2>
              {rfq.rfq_subject && (
                <p>
                  <b>Тема:</b> {rfq.rfq_subject}
                </p>
              )}
              {rfq.rfq_body ? (
                <>
                  <pre className="letter">{rfq.rfq_body}</pre>
                  <div className="actions">
                    <button
                      className="secondary"
                      onClick={() => void navigator.clipboard.writeText(rfq.rfq_body ?? "")}
                    >
                      Скопировать текст
                    </button>
                  </div>
                </>
              ) : (
                <p className="note">Текст RFQ не сгенерирован.</p>
              )}
            </div>
          )}

          {tab === "suppliers" && (
            <div className="panel">
              <h2>Поставщики</h2>
              <p className="note">
                Поиск поставщиков и выбор получателей — шаг 4 плана внедрения UI.
              </p>
            </div>
          )}

          {tab === "dispatch" && (
            <div className="panel">
              <h2>Рассылка</h2>
              <p className="note">
                Отправка по каналам и статусы доставки — шаг 4 плана внедрения UI.
              </p>
            </div>
          )}

          {tab === "replies" && (
            <ExtractReplies
              rfqId={rfq.id}
              onStored={() => setRefreshKey((k) => k + 1)}
            />
          )}

          {tab === "summary" && <Summary rfqId={rfq.id} refreshKey={refreshKey} />}

          {tab === "history" && (
            <div className="panel">
              <h2>История</h2>
              <p className="note">
                Переписка по треду и журнал действий — шаг 5 плана внедрения UI.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function VerificationTab({ rfq }: { rfq: RFQRead }) {
  const [history, setHistory] = useState<PriceHistoryItem[] | null>(null);

  useEffect(() => {
    api
      .priceHistory(rfq.cas)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [rfq.cas]);

  const v = rfq.verification;
  // История без котировок текущего запроса.
  const past = (history ?? []).filter((h) => h.rfq_id !== rfq.id);

  return (
    <>
      <div className="panel">
        <h2>
          Верификация вещества{" "}
          <span className="badge tone-neutral" title="Echemi — в разработке">
            Echemi: демо
          </span>
        </h2>
        {v?.found ? (
          <dl className="params-list">
            <dt>Название (IUPAC)</dt>
            <dd>{(v.iupac_name as string) ?? "—"}</dd>
            <dt>Формула</dt>
            <dd>{(v.molecular_formula as string) ?? "—"}</dd>
            <dt>Молекулярная масса</dt>
            <dd>{(v.molecular_weight as number) ?? "—"}</dd>
            <dt>Синонимы</dt>
            <dd>{((v.synonyms as string[]) ?? []).slice(0, 6).join(", ") || "—"}</dd>
            <dt>Источник</dt>
            <dd>{(v.source as string) ?? "PubChem"}</dd>
          </dl>
        ) : (
          <p className="note">
            Вещество не верифицировано{v?.error ? ` (${v.error})` : ""}. Проверка
            выполняется при создании запроса.
          </p>
        )}
      </div>

      <div className="panel">
        <h2>История закупочных цен по CAS {rfq.cas}</h2>
        {history === null && <p className="note">Загрузка…</p>}
        {history !== null && past.length === 0 && (
          <p className="note">Прошлых закупок по этому веществу не найдено.</p>
        )}
        {past.length > 0 && (
          <table className="summary">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Цена</th>
                <th>Базис</th>
                <th>MOQ</th>
                <th>Запрос</th>
              </tr>
            </thead>
            <tbody>
              {past.map((h, i) => (
                <tr key={i}>
                  <td>{h.date}</td>
                  <td>
                    {h.price} {h.currency ?? ""}
                  </td>
                  <td>{h.incoterm ?? "—"}</td>
                  <td>{h.moq ?? "—"}</td>
                  <td>#{h.rfq_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
