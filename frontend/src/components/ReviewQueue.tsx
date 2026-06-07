// Раздел «Ручной разбор» (раздел 13 UI/UX-плана): очередь кейсов,
// назначение ответственных (руководитель), закрытие с результатом.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { EscalationRead, UserAdminRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const REASON_LABELS: Record<string, string> = {
  grade: "Нестандартный грейд",
  logistics: "Опасная логистика",
  shortage: "Дефицит",
  custom_synthesis: "Кастомный синтез",
  low_confidence: "Низкая уверенность извлечения",
  other: "Другое",
};

const STATUS_LABELS: Record<string, string> = {
  open: "открыт",
  in_progress: "в работе",
  resolved: "решён",
};

export default function ReviewQueue({
  onOpenRfq,
}: {
  onOpenRfq: (id: number) => void;
}) {
  const { user } = useAuth();
  const canAssign = user?.role === "head" || user?.role === "admin";
  const readOnly = user?.role === "auditor";

  const [items, setItems] = useState<EscalationRead[]>([]);
  const [users, setUsers] = useState<UserAdminRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reasonFilter, setReasonFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("open_or_progress");
  const [closing, setClosing] = useState<EscalationRead | null>(null);
  const [closeNote, setCloseNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setItems(await api.listEscalationQueue());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    if (canAssign) {
      api
        .listUsers()
        .then((all) => setUsers(all.filter((u) => u.is_active)))
        .catch(() => setUsers([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canAssign]);

  const filtered = useMemo(() => {
    let out = items;
    if (statusFilter === "open_or_progress")
      out = out.filter((e) => e.status !== "resolved");
    else if (statusFilter) out = out.filter((e) => e.status === statusFilter);
    if (reasonFilter) out = out.filter((e) => e.reason === reasonFilter);
    return out;
  }, [items, reasonFilter, statusFilter]);

  const assign = async (esc: EscalationRead, assignee: string) => {
    setBusy(true);
    try {
      await api.updateEscalation(esc.id, { assignee });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const takeSelf = async (esc: EscalationRead) => {
    if (!user) return;
    await assign(esc, user.full_name);
  };

  const close = async () => {
    if (!closing) return;
    setBusy(true);
    try {
      await api.updateEscalation(closing.id, {
        status: "resolved",
        note: closeNote.trim() || undefined,
      });
      setClosing(null);
      setCloseNote("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="requests-page">
      <div className="requests-header">
        <h1>Ручной разбор</h1>
      </div>

      <div className="requests-filters">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="open_or_progress">Статус: активные</option>
          <option value="">Статус: все</option>
          <option value="open">открыт</option>
          <option value="in_progress">в работе</option>
          <option value="resolved">решён</option>
        </select>
        <select value={reasonFilter} onChange={(e) => setReasonFilter(e.target.value)}>
          <option value="">Причина: все</option>
          {Object.entries(REASON_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error">{error}</p>}

      {closing && (
        <div className="panel escalate-form">
          <h2>
            Закрыть кейс #{closing.id} · {closing.rfq_name}
          </h2>
          <div className="field">
            <label>Результат разбора</label>
            <input
              value={closeNote}
              onChange={(e) => setCloseNote(e.target.value)}
              placeholder="Что решили и почему"
              autoFocus
            />
          </div>
          <div className="actions">
            <button onClick={() => void close()} disabled={busy}>
              Закрыть кейс
            </button>
            <button className="secondary" onClick={() => setClosing(null)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="panel">
          <p className="note">Кейсов по выбранным фильтрам нет.</p>
        </div>
      ) : (
        <div className="panel table-panel">
          <table className="summary requests-table">
            <thead>
              <tr>
                <th>Запрос</th>
                <th>Причина</th>
                <th>Комментарий</th>
                <th>Создано</th>
                <th>Кому</th>
                <th>Статус</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <tr key={e.id}>
                  <td>
                    <a
                      className="link"
                      onClick={() => onOpenRfq(e.rfq_id)}
                      title="Открыть карточку запроса"
                    >
                      #{e.rfq_id} · {e.rfq_name ?? "—"}
                    </a>
                    {e.rfq_cas && <div className="cas">CAS {e.rfq_cas}</div>}
                  </td>
                  <td>{REASON_LABELS[e.reason] ?? e.reason}</td>
                  <td className="note">{e.note ?? "—"}</td>
                  <td className="note">
                    {new Date(e.created_at).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </td>
                  <td>
                    {canAssign && e.status !== "resolved" ? (
                      <select
                        value={e.assignee ?? ""}
                        disabled={busy}
                        onChange={(ev) => void assign(e, ev.target.value)}
                      >
                        <option value="" disabled>
                          — назначить
                        </option>
                        {users.map((u) => (
                          <option key={u.id} value={u.full_name}>
                            {u.full_name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      e.assignee ?? "— не назначен"
                    )}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        e.status === "resolved"
                          ? "tone-ok"
                          : e.status === "in_progress"
                            ? "tone-info"
                            : "tone-warn"
                      }`}
                    >
                      {STATUS_LABELS[e.status] ?? e.status}
                    </span>
                  </td>
                  <td>
                    {!readOnly && e.status !== "resolved" && (
                      <div className="row-actions">
                        {!canAssign && e.assignee !== user?.full_name && (
                          <button
                            className="secondary btn-small"
                            disabled={busy}
                            onClick={() => void takeSelf(e)}
                          >
                            Взять
                          </button>
                        )}
                        <button
                          className="secondary btn-small"
                          onClick={() => {
                            setClosing(e);
                            setCloseNote("");
                          }}
                        >
                          Закрыть
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
