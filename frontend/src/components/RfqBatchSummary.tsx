// Сводка пакета закупки: позиции одного списка и их состояние.
//
// Пакет связывает запросы, но не объединяет их: у каждой позиции свой
// поиск, свои поставщики и своя котировка. Отсюда открывается отдельный
// запрос, а из запроса — возврат обратно к списку.

import { useEffect, useState } from "react";

import { api, userErrorMessage } from "../api/client";
import type { RFQStatus, RfqBatchSummary as Summary } from "../api/types";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";

interface Props {
  batchId: number;
  onOpenRfq: (rfqId: number) => void;
  onBack: () => void;
}

export default function RfqBatchSummary({ batchId, onOpenRfq, onBack }: Props) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.getRfqBatch(batchId);
        if (!cancelled) {
          setSummary(data);
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
          показывает, что заведено одним списком.
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
    </div>
  );
}
