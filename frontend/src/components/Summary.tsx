import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { EscalationRead, SummaryRow } from "../api/types";

interface Props {
  rfqId: number;
  refreshKey: number;
}

const REASON_LABELS: Record<string, string> = {
  custom_synthesis: "кастом-синтез",
  shortage: "дефицит",
  logistics: "логистика",
  low_confidence: "низкая уверенность",
  grade: "грейд",
  other: "прочее",
};

export default function Summary({ rfqId, refreshKey }: Props) {
  const [rows, setRows] = useState<SummaryRow[]>([]);
  const [escalations, setEscalations] = useState<EscalationRead[]>([]);
  const [onlyComplete, setOnlyComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setRows(await api.getSummary(rfqId));
        setEscalations(await api.listEscalations(rfqId));
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [rfqId, refreshKey]);

  const shown = onlyComplete ? rows.filter((r) => r.is_complete) : rows;

  return (
    <>
      <div className="panel">
        <h2>Сводная сравнительная таблица</h2>
        {error && <p className="error">{error}</p>}

        <div className="checks" style={{ marginBottom: 8 }}>
          <label>
            <input
              type="checkbox"
              checked={onlyComplete}
              onChange={(e) => setOnlyComplete(e.target.checked)}
            />
            Только полные котировки
          </label>
        </div>

        {shown.length === 0 ? (
          <p className="note">Котировок пока нет — извлеките ответы поставщиков выше.</p>
        ) : (
          <table className="summary">
            <thead>
              <tr>
                <th>Поставщик</th>
                <th>Цена</th>
                <th>Вал.</th>
                <th>Базис</th>
                <th>MOQ</th>
                <th>Грейд</th>
                <th>Срок</th>
                <th>CoA</th>
                <th>TDS</th>
                <th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.quotation_id} className={r.is_complete ? "" : "incomplete"}>
                  <td>{r.supplier ?? r.manager ?? "—"}</td>
                  <td>{r.price ?? "—"}</td>
                  <td>{r.currency ?? "—"}</td>
                  <td>{r.incoterm ?? "—"}</td>
                  <td>{r.moq ?? "—"}</td>
                  <td>{r.grade ?? "—"}</td>
                  <td>{r.lead_time ?? "—"}</td>
                  <td>{r.has_coa ? "✓" : "—"}</td>
                  <td>{r.has_tds ? "✓" : "—"}</td>
                  <td>
                    <span className={`badge ${r.is_complete ? "ok" : "err"}`}>
                      {r.is_complete ? "полная" : "неполная"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>Очередь эскалаций</h2>
        {escalations.length === 0 ? (
          <p className="note">Нет эскалаций.</p>
        ) : (
          escalations.map((e) => (
            <div key={e.id} className="rfq-list-item" style={{ cursor: "default" }}>
              <div>
                <span className="badge err">{REASON_LABELS[e.reason] ?? e.reason}</span>{" "}
                <span className="badge muted">{e.status}</span>
              </div>
              {e.note && <div className="cas">{e.note}</div>}
            </div>
          ))
        )}
      </div>
    </>
  );
}
