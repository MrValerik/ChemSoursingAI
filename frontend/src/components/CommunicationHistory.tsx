import { useEffect, useState } from "react";
import { api, userErrorMessage } from "../api/client";
import type { CommunicationRead, EmailSyncRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const STATUS_LABELS: Record<string, string> = {
  draft: "черновик",
  sent: "отправлено",
  received: "получено",
  error: "ошибка",
};

export default function CommunicationHistory({
  rfqId,
  onSynced,
}: {
  rfqId: number;
  onSynced: () => void;
}) {
  const { user } = useAuth();
  const [items, setItems] = useState<CommunicationRead[]>([]);
  const [syncResult, setSyncResult] = useState<EmailSyncRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setItems(await api.listCommunications(rfqId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId]);

  const sync = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.syncEmail();
      setSyncResult(result);
      await load();
      onSynced();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const sendDraft = async (id: number) => {
    setBusy(true);
    setError(null);
    try {
      await api.sendCommunicationDraft(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <div className="tab-toolbar">
        <div>
          <h2>Email-переписка</h2>
          <p className="note">
            Ответы автоматически связываются с запросом по служебной метке и Message-ID. Неполный
            ответ создаёт дозапрос-черновик.
          </p>
        </div>
        {(user?.role === "head" || user?.role === "admin") && (
          <button disabled={busy} onClick={() => void sync()}>
            {busy ? "Синхронизация…" : "Загрузить новые Email"}
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}
      {syncResult && (
        <div className="sync-result note">
          Получено: {syncResult.fetched} · обработано: {syncResult.processed} ·
          котировок: {syncResult.quotations_created} · черновиков:{" "}
          {syncResult.followups_drafted}
          {syncResult.unmatched > 0 && (
            <> · без связи с запросом: {syncResult.unmatched}</>
          )}
          {syncResult.errors.length > 0 && (
            <div className="error">
              {syncResult.errors.map((item) => userErrorMessage(item)).join("; ")}
            </div>
          )}
        </div>
      )}

      {items.length === 0 ? (
        <p className="note">Переписки по этому запросу пока нет.</p>
      ) : (
        <div className="communication-list">
          {items.map((item) => (
            <article
              key={item.id}
              className={`communication-card ${item.direction}`}
            >
              <div className="communication-head">
                <div>
                  <b>{item.direction === "inbound" ? "← Входящее" : "→ Исходящее"}</b>
                  {" · "}
                  {item.subject || "Без темы"}
                </div>
                <span
                  className={`badge tone-${
                    item.status === "error"
                      ? "danger"
                      : item.status === "draft"
                        ? "warn"
                        : "ok"
                  }`}
                >
                  {STATUS_LABELS[item.status ?? ""] ?? item.status ?? "—"}
                </span>
              </div>
              <div className="note">
                {item.from_address || "—"} → {item.to_address || "—"} ·{" "}
                {new Date(item.created_at).toLocaleString("ru-RU")}
              </div>
              <pre className="communication-body">{item.body || "—"}</pre>
              {(item.attachments?.length ?? 0) > 0 && (
                <div className="note">
                  Вложения:{" "}
                  {item.attachments
                    ?.map((file) => `${file.filename} (${Math.ceil(file.size / 1024)} КБ)`)
                    .join(", ")}
                </div>
              )}
              {item.status === "draft" && user?.role !== "auditor" && (
                <div className="actions">
                  <button
                    disabled={busy}
                    onClick={() => void sendDraft(item.id)}
                  >
                    Отправить черновик
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
