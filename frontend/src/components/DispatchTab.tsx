// Вкладка «Рассылка» (раздел 10 UI/UX-плана): получатели и статусы доставки.
// Отправка — демо-режим до подключения Email/WhatsApp-коннекторов.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DispatchStatusKind, RecipientRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const STATUS_LABELS: Record<DispatchStatusKind, string> = {
  queued: "в очереди",
  sent: "отправлено",
  delivered: "доставлено",
  read: "прочитано",
  error: "ошибка",
};

const STATUS_TONE: Record<DispatchStatusKind, string> = {
  queued: "neutral",
  sent: "info",
  delivered: "ok",
  read: "ok",
  error: "warn",
};

export default function DispatchTab({
  rfqId,
  onStatusChanged,
}: {
  rfqId: number;
  onStatusChanged: () => void;
}) {
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";

  const [recipients, setRecipients] = useState<RecipientRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setRecipients(await api.listRecipients(rfqId));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId]);

  const queued = recipients.filter((r) => r.status === "queued");

  const dispatch = async () => {
    setBusy(true);
    setError(null);
    try {
      setRecipients(await api.dispatchRfq(rfqId));
      onStatusChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setError(null);
    try {
      await api.removeRecipient(rfqId, id);
      await load();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="panel">
      <div className="tab-toolbar">
        <h2>Рассылка RFQ</h2>
        {!readOnly && (
          <button onClick={() => void dispatch()} disabled={busy || queued.length === 0}>
            {busy ? "Отправка…" : `Разослать выбранным (${queued.length})`}
          </button>
        )}
      </div>
      <p className="note">
        Демо-режим: реальная отправка появится с подключением Email/WhatsApp;
        тогда статусы продолжат путь отправлено → доставлено → прочитано.
      </p>

      {error && <p className="error">{error}</p>}

      {recipients.length === 0 ? (
        <p className="note">
          Получатели не выбраны — отметьте поставщиков на вкладке «Поставщики».
        </p>
      ) : (
        <table className="summary">
          <thead>
            <tr>
              <th>Получатель</th>
              <th>Канал</th>
              <th>Статус</th>
              <th>Примечание</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {recipients.map((r) => (
              <tr key={r.id}>
                <td>{r.supplier_company ?? `Поставщик #${r.supplier_id}`}</td>
                <td>{r.channel}</td>
                <td>
                  <span className={`badge tone-${STATUS_TONE[r.status]}`}>
                    {STATUS_LABELS[r.status]}
                  </span>
                </td>
                <td className="note">{r.note ?? "—"}</td>
                <td>
                  {!readOnly && r.status === "queued" && (
                    <button
                      className="secondary btn-small"
                      onClick={() => void remove(r.id)}
                    >
                      Убрать
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
