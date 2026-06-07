// Дашборд по ролям (раздел 5 UI/UX-плана): закупщик — «мой день»,
// руководитель/админ/аудитор — срез отдела с нагрузкой и просрочками.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DashboardData } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";
import type { RFQStatus } from "../api/types";

export default function Dashboard({
  onOpenRfq,
  onGoToRequests,
}: {
  onOpenRfq: (id: number) => void;
  onGoToRequests: () => void;
}) {
  const { user } = useAuth();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .dashboard()
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="error" style={{ padding: 24 }}>{error}</p>;
  if (!data) return <p className="note" style={{ padding: 24 }}>Загрузка…</p>;

  const seeAll = data.role !== "buyer";
  const maxWorkload = Math.max(1, ...(data.workload ?? []).map((w) => w.count));

  return (
    <div className="requests-page">
      <div className="requests-header">
        <h1>{seeAll ? "Дашборд отдела" : `Мой день — ${user?.full_name}`}</h1>
      </div>

      <div className="dash-cards">
        <button className="dash-card" onClick={onGoToRequests}>
          <div className="dash-value">{data.in_work}</div>
          <div className="dash-label">В работе</div>
        </button>
        <button className="dash-card attention" onClick={onGoToRequests}>
          <div className="dash-value">{data.attention}</div>
          <div className="dash-label">Требуют внимания</div>
        </button>
        <button className="dash-card warn" onClick={onGoToRequests}>
          <div className="dash-value">{data.manual_review}</div>
          <div className="dash-label">На ручном разборе</div>
        </button>
      </div>

      <div className="dash-columns">
        <div className="panel">
          <h2>{seeAll ? "Запросы по статусам" : "Мои запросы по статусам"}</h2>
          {Object.keys(data.by_status).length === 0 ? (
            <p className="note">Запросов пока нет.</p>
          ) : (
            <div className="dash-statuses">
              {Object.entries(data.by_status).map(([status, count]) => (
                <div key={status} className="dash-status-row">
                  <span
                    className={`badge tone-${STATUS_TONE[status as RFQStatus] ?? "neutral"}`}
                  >
                    {STATUS_LABELS[status as RFQStatus] ?? status}
                  </span>
                  <b>{count}</b>
                </div>
              ))}
            </div>
          )}
        </div>

        {seeAll && (
          <div className="panel">
            <h2>Нагрузка по закупщикам</h2>
            {!data.workload || data.workload.length === 0 ? (
              <p className="note">Активных запросов нет.</p>
            ) : (
              data.workload.map((w) => (
                <div key={w.owner} className="workload-row">
                  <span className="workload-name">{w.owner}</span>
                  <div className="workload-bar">
                    <div
                      className="workload-fill"
                      style={{ width: `${(100 * w.count) / maxWorkload}%` }}
                    />
                  </div>
                  <b>{w.count}</b>
                </div>
              ))
            )}
          </div>
        )}

        <div className="panel">
          <h2>Просрочки / SLA (&gt; 3 дней без сводки)</h2>
          {data.overdue.length === 0 ? (
            <p className="note">Просроченных запросов нет.</p>
          ) : (
            <table className="summary">
              <tbody>
                {data.overdue.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <a className="link" onClick={() => onOpenRfq(o.id)}>
                        #{o.id} · {o.name}
                      </a>
                      {seeAll && o.owner_name && (
                        <div className="cas">{o.owner_name}</div>
                      )}
                    </td>
                    <td>
                      <span className={`badge tone-${STATUS_TONE[o.status]}`}>
                        {STATUS_LABELS[o.status]}
                      </span>
                    </td>
                    <td className="note">{o.age_days} дн.</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
