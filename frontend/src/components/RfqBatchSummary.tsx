// Сводка пакета закупки: позиции одного списка и их состояние.
//
// У каждой позиции свой поиск и своя котировка. Только явно выбранные позиции
// можно объединить в одно сообщение общему поставщику.

import { useEffect, useState } from "react";

import { api, userErrorMessage } from "../api/client";
import type {
  CombinedRfqOption,
  CombinedRfqPreview,
  RFQStatus,
  RfqBatchSummary as Summary,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";

interface Props {
  batchId: number;
  onOpenRfq: (rfqId: number) => void;
  onBack: () => void;
}

const optionKey = (option: CombinedRfqOption) =>
  `${option.supplier_id}:${option.channel}`;

const newActionId = () => globalThis.crypto.randomUUID();

export default function RfqBatchSummary({ batchId, onOpenRfq, onBack }: Props) {
  const { user } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [combinedOptions, setCombinedOptions] = useState<CombinedRfqOption[]>([]);
  const [selectedRfqs, setSelectedRfqs] = useState<Record<string, number[]>>({});
  const [previews, setPreviews] = useState<Record<string, CombinedRfqPreview>>({});
  const [actionIds, setActionIds] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const [batch, options] = await Promise.all([
      api.getRfqBatch(batchId),
      api.combinedCommunicationOptions(batchId),
    ]);
    setSummary(batch);
    setCombinedOptions(options);
    setError(null);
  };

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [data, options] = await Promise.all([
          api.getRfqBatch(batchId),
          api.combinedCommunicationOptions(batchId),
        ]);
        if (!cancelled) {
          setSummary(data);
          setCombinedOptions(options);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) setError(userErrorMessage(caught));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  if (error) {
    return (
      <div className="requests-page">
        <button className="secondary back-btn" onClick={onBack}>
          ← К запросам
        </button>
        <p className="error">{error}</p>
      </div>
    );
  }
  if (!summary) {
    return <p className="note" style={{ padding: 24 }}>Загрузка…</p>;
  }

  const queued = summary.items.reduce((sum, item) => sum + item.search_runs, 0);

  const togglePosition = (key: string, rfqId: number) => {
    setSelectedRfqs((current) => {
      const selected = current[key] ?? [];
      return {
        ...current,
        [key]: selected.includes(rfqId)
          ? selected.filter((id) => id !== rfqId)
          : [...selected, rfqId],
      };
    });
    setPreviews((current) => {
      const updated = { ...current };
      delete updated[key];
      return updated;
    });
    setActionIds((current) => {
      const updated = { ...current };
      delete updated[key];
      return updated;
    });
    setNotice(null);
  };

  const previewCombined = async (option: CombinedRfqOption) => {
    const key = optionKey(option);
    const rfqIds = selectedRfqs[key] ?? [];
    if (rfqIds.length < 2) return;
    setBusyKey(key);
    setError(null);
    try {
      const preview = await api.previewCombinedCommunication(batchId, {
        supplier_id: option.supplier_id,
        channel: option.channel,
        rfq_ids: rfqIds,
      });
      setPreviews((current) => ({ ...current, [key]: preview }));
      setActionIds((current) => ({
        ...current,
        [key]: current[key] ?? newActionId(),
      }));
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setBusyKey(null);
    }
  };

  const sendCombined = async (option: CombinedRfqOption) => {
    const key = optionKey(option);
    const preview = previews[key];
    const idempotencyKey = actionIds[key];
    if (!preview || !idempotencyKey) return;
    if (
      !window.confirm(
        `Отправить одно общее RFQ компании «${option.supplier_company}» по ${preview.rfq_ids.length} явно выбранным позициям? При включённом канале это реальная внешняя отправка.`,
      )
    ) {
      return;
    }
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      const result = await api.sendCombinedCommunication(batchId, {
        supplier_id: option.supplier_id,
        channel: option.channel,
        rfq_ids: preview.rfq_ids,
        idempotency_key: idempotencyKey,
        confirm_external_send: true,
      });
      setNotice(
        `Общее RFQ для ${result.rfq_ids.length} позиций сохранено со статусом «${result.status}».`,
      );
      setSelectedRfqs((current) => ({ ...current, [key]: [] }));
      setPreviews((current) => {
        const updated = { ...current };
        delete updated[key];
        return updated;
      });
      await load();
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div className="requests-page">
      <button className="secondary back-btn" onClick={onBack}>
        ← К запросам
      </button>
      <div className="panel">
        <h2>
          Пакет №{summary.batch_id}
          {summary.source_name ? ` · ${summary.source_name}` : ""}
        </h2>
        <p className="rfq-import-summary">
          Позиций в пакете: <strong>{summary.total}</strong> · поисков в
          очереди: <strong>{queued}</strong>
          {summary.hidden > 0 && (
            <>
              {" "}
              · скрыто по правам доступа: <strong>{summary.hidden}</strong>
            </>
          )}
        </p>
        <p className="rfq-import-hint">
          Позиции независимы: у каждой свой поиск и своя котировка. Пакет
          показывает, что заведено одним списком, и позволяет отправить общий
          запрос подтверждённому для нескольких позиций поставщику.
        </p>

        <div className="rfq-import-table-wrap">
          <table className="rfq-import-table">
            <thead>
              <tr>
                <th>Запрос</th>
                <th>Вещество</th>
                <th>CAS</th>
                <th>Объём</th>
                <th>Статус</th>
                <th>Поисков</th>
              </tr>
            </thead>
            <tbody>
              {summary.items.map((item) => (
                <tr key={item.rfq_id}>
                  <td>
                    <button
                      className="link-btn"
                      onClick={() => onOpenRfq(item.rfq_id)}
                    >
                      №{item.rfq_id}
                    </button>
                  </td>
                  <td>{item.name}</td>
                  <td>{item.cas || <span className="rfq-import-none">—</span>}</td>
                  <td>{item.volume || <span className="rfq-import-none">—</span>}</td>
                  <td>
                    <span
                      className={`badge tone-${
                        STATUS_TONE[item.status as RFQStatus] ?? "neutral"
                      }`}
                    >
                      {STATUS_LABELS[item.status as RFQStatus] ?? item.status}
                    </span>
                  </td>
                  <td>{item.search_runs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <section className="panel combined-rfq-panel">
        <h2>Общее RFQ одному поставщику</h2>
        <p className="note">
          Здесь показаны только поставщики, которых вы уже выбрали минимум для
          двух позиций. Отметьте позиции явно: похожие названия автоматически не
          объединяются.
        </p>
        {combinedOptions.length === 0 ? (
          <p className="note">
            Пока нет общего поставщика с двумя позициями в очереди отправки.
          </p>
        ) : (
          <div className="combined-rfq-options">
            {combinedOptions.map((option) => {
              const key = optionKey(option);
              const selected = selectedRfqs[key] ?? [];
              const preview = previews[key];
              return (
                <article className="combined-rfq-option" key={key}>
                  <div>
                    <strong>{option.supplier_company}</strong>
                    <span className="badge tone-neutral">
                      {option.channel === "email" ? "Email" : "WhatsApp"}
                    </span>
                  </div>
                  <div className="combined-rfq-positions">
                    {option.positions.map((position) => (
                      <label key={position.rfq_id}>
                        <input
                          checked={selected.includes(position.rfq_id)}
                          disabled={busyKey === key || user?.role === "auditor"}
                          type="checkbox"
                          onChange={() => togglePosition(key, position.rfq_id)}
                        />
                        <span>
                          RFQ-{position.rfq_id} · {position.name}
                          {position.cas ? ` · CAS ${position.cas}` : ""}
                          {position.volume ? ` · ${position.volume}` : ""}
                        </span>
                      </label>
                    ))}
                  </div>
                  {user?.role !== "auditor" && (
                    <button
                      className="secondary"
                      disabled={selected.length < 2 || busyKey === key}
                      type="button"
                      onClick={() => void previewCombined(option)}
                    >
                      {busyKey === key ? "Подготовка…" : "Показать общее письмо"}
                    </button>
                  )}
                  {preview && (
                    <div className="combined-rfq-preview">
                      {preview.subject && (
                        <div>
                          <span className="note">Тема Email</span>
                          <strong>{preview.subject}</strong>
                        </div>
                      )}
                      <div>
                        <span className="note">Точный текст сообщения</span>
                        <pre>{preview.body}</pre>
                      </div>
                      <button
                        disabled={busyKey === key}
                        type="button"
                        onClick={() => void sendCombined(option)}
                      >
                        {busyKey === key
                          ? "Отправка…"
                          : `Подтвердить отправку (${preview.rfq_ids.length})`}
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
        {notice && <p className="success-note">{notice}</p>}
        {error && <p className="error">{error}</p>}
      </section>
    </div>
  );
}
