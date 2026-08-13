import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SummaryRow } from "../api/types";

interface Props {
  rfqId: number;
  // Пересобрать таблицу без размонтирования вкладки.
  refreshKey?: number;
}

export default function Summary({ rfqId, refreshKey = 0 }: Props) {
  const [rows, setRows] = useState<SummaryRow[]>([]);
  const [onlyComplete, setOnlyComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setRows(await api.getSummary(rfqId));
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [rfqId, refreshKey]);

  const shown = onlyComplete ? rows.filter((r) => r.is_complete) : rows;

  return (
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
  );
}
