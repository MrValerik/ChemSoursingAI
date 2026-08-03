// Вкладка «Общение»: история диалогов с поставщиками и очередь эскалаций.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  CommunicationEscalationRead,
  CommunicationOverviewRead,
  RFQRead,
  SupplierConversationRead,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";

const EMPTY_OVERVIEW: CommunicationOverviewRead = {
  conversations: [],
  unassigned_escalations: [],
};

const conversationKey = (item: SupplierConversationRead) =>
  `${item.supplier_id ?? item.contact ?? "unknown"}:${item.channel}`;

const formatMoment = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

export default function DispatchTab({
  rfq,
  onStatusChanged,
}: {
  rfq: RFQRead;
  onStatusChanged: () => void;
}) {
  const rfqId = rfq.id;
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";

  const [overview, setOverview] = useState<CommunicationOverviewRead>(EMPTY_OVERVIEW);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [escalationBusy, setEscalationBusy] = useState<number | null>(null);

  const canSyncEmail = user?.role === "head" || user?.role === "admin";

  const load = async () => {
    try {
      const communicationItems = await api.communicationOverview(rfqId);
      setOverview(communicationItems);
      setSelectedKey((current) => {
        if (
          current &&
          communicationItems.conversations.some(
            (item) => conversationKey(item) === current,
          )
        ) {
          return current;
        }
        return communicationItems.conversations[0]
          ? conversationKey(communicationItems.conversations[0])
          : null;
      });
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId]);

  const selectedConversation =
    overview.conversations.find((item) => conversationKey(item) === selectedKey) ??
    null;
  const activeEscalations = [
    ...overview.unassigned_escalations,
    ...overview.conversations.flatMap((item) => item.escalations),
  ].filter((item) => item.status !== "resolved");

  const syncEmail = async () => {
    setSyncing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.syncEmailCommunications();
      const syncErrors = result.errors;
      setNotice(
        `Проверено писем: ${result.fetched}. Обработано: ${result.processed}. ` +
          `Новых эскалаций: ${result.escalations_created}.`,
      );
      await load();
      if (syncErrors.length > 0) {
        setError(syncErrors.join("; "));
      }
      onStatusChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSyncing(false);
    }
  };

  const updateEscalation = async (
    escalation: CommunicationEscalationRead,
    action: "take" | "resolve",
  ) => {
    if (!user) return;
    setEscalationBusy(escalation.id);
    setError(null);
    try {
      await api.updateEscalation(
        escalation.id,
        action === "take"
          ? { assignee: user.full_name }
          : { status: "resolved" },
      );
      await load();
      onStatusChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setEscalationBusy(null);
    }
  };

  return (
    <div>
      <div className="panel communication-panel">
        <div className="tab-toolbar">
          <div>
            <h2>Диалоги с поставщиками</h2>
            <p className="note">
              Входящие сообщения, ответы и вопросы, переданные сотруднику.
            </p>
          </div>
          <div className="communication-toolbar-actions">
            {activeEscalations.length > 0 && (
              <span className="badge tone-warn" role="status">
                Требуют ответа: {activeEscalations.length}
              </span>
            )}
            {canSyncEmail && (
              <button
                className="secondary"
                disabled={syncing}
                onClick={() => void syncEmail()}
              >
                {syncing ? "Проверка…" : "Проверить входящие Email"}
              </button>
            )}
          </div>
        </div>

        {notice && <p className="success-note">{notice}</p>}
        {error && <p className="error">{error}</p>}

        {overview.unassigned_escalations
          .filter((item) => item.status !== "resolved")
          .map((item) => (
            <EscalationNotice
              key={item.id}
              escalation={item}
              busy={escalationBusy === item.id}
              readOnly={readOnly}
              onAction={updateEscalation}
            />
          ))}

        {overview.conversations.length === 0 ? (
          <p className="note">
            Диалогов пока нет. После отправки запроса или получения ответа
            поставщик появится здесь.
          </p>
        ) : (
          <div className="conversation-layout">
            <div className="conversation-suppliers" aria-label="Поставщики">
              {overview.conversations.map((item) => {
                const openCount = item.escalations.filter(
                  (entry) => entry.status !== "resolved",
                ).length;
                const lastMessage =
                  item.messages[item.messages.length - 1]?.body ??
                  "Сообщений пока нет";
                return (
                  <button
                    className={`conversation-supplier ${
                      conversationKey(item) === selectedKey ? "active" : ""
                    }`}
                    key={conversationKey(item)}
                    onClick={() => setSelectedKey(conversationKey(item))}
                    type="button"
                  >
                    <span className="conversation-supplier-title">
                      {item.supplier_company}
                    </span>
                    <span className="conversation-supplier-meta">
                      {item.channel === "email" ? "Email" : "WhatsApp"}
                      {item.manager_name ? ` · ${item.manager_name}` : ""}
                    </span>
                    <span className="conversation-preview">{lastMessage}</span>
                    {openCount > 0 && (
                      <span className="badge tone-warn">
                        Нужен человек · {openCount}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            <div className="conversation-thread">
              {selectedConversation && (
                <>
                  <div className="conversation-thread-header">
                    <div>
                      <strong>{selectedConversation.supplier_company}</strong>
                      <div className="note">
                        {selectedConversation.contact ?? "Контакт не указан"}
                      </div>
                    </div>
                    <span className="badge tone-neutral">
                      {selectedConversation.channel === "email"
                        ? "Email"
                        : "WhatsApp"}
                    </span>
                  </div>

                  {selectedConversation.escalations
                    .filter((item) => item.status !== "resolved")
                    .map((item) => (
                      <EscalationNotice
                        key={item.id}
                        escalation={item}
                        busy={escalationBusy === item.id}
                        readOnly={readOnly}
                        onAction={updateEscalation}
                      />
                    ))}

                  <div className="conversation-messages">
                    {selectedConversation.messages.length === 0 ? (
                      <p className="note">Сообщения ещё не сохранены.</p>
                    ) : (
                      selectedConversation.messages.map((message) => (
                        <article
                          className={`conversation-message ${message.direction}`}
                          key={message.id}
                        >
                          <div className="conversation-message-meta">
                            <strong>
                              {message.direction === "inbound"
                                ? "Поставщик"
                                : "Мы"}
                            </strong>
                            <span>{formatMoment(message.created_at)}</span>
                          </div>
                          <p>{message.body || "—"}</p>
                          {message.status && (
                            <span className="conversation-delivery-status">
                              {message.status === "demo"
                                ? "демонстрация"
                                : message.status}
                            </span>
                          )}
                        </article>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

    </div>
  );
}

function EscalationNotice({
  escalation,
  busy,
  readOnly,
  onAction,
}: {
  escalation: CommunicationEscalationRead;
  busy: boolean;
  readOnly: boolean;
  onAction: (
    escalation: CommunicationEscalationRead,
    action: "take" | "resolve",
  ) => Promise<void>;
}) {
  return (
    <section className="communication-escalation" role="alert">
      <div>
        <strong>Нужен ответ сотрудника</strong>
        <p>{escalation.note ?? "Автоматический ответ остановлен."}</p>
        {escalation.message_body && (
          <blockquote>{escalation.message_body}</blockquote>
        )}
        <span className="note">
          {escalation.assignee
            ? `Ответственный: ${escalation.assignee}`
            : "Ответственный ещё не назначен"}
        </span>
      </div>
      {!readOnly && (
        <div className="communication-escalation-actions">
          {!escalation.assignee && (
            <button
              className="secondary btn-small"
              disabled={busy}
              onClick={() => void onAction(escalation, "take")}
              type="button"
            >
              Взять в работу
            </button>
          )}
          <button
            className="secondary btn-small"
            disabled={busy}
            onClick={() => void onAction(escalation, "resolve")}
            type="button"
          >
            Отметить решённой
          </button>
        </div>
      )}
    </section>
  );
}
