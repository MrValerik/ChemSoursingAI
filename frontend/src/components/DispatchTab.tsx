// Вкладка «Общение»: история диалогов с поставщиками и очередь эскалаций.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  CommunicationAttachmentRead,
  CommunicationEscalationRead,
  CommunicationOverviewRead,
  RFQRead,
  SupplierConversationRead,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import RfqDispatchPreparation from "./RfqDispatchPreparation";
import CommunicationTesting from "./CommunicationTesting";
import { Textarea } from "./ui";

const EMPTY_OVERVIEW: CommunicationOverviewRead = {
  conversations: [],
  unassigned_escalations: [],
};

const conversationKey = (item: SupplierConversationRead) =>
  `${item.supplier_id ?? item.contact ?? "unknown"}:${item.channel}`;

const QUOTE_FIELD_LABELS: Record<string, string> = {
  price: "цена",
  incoterm: "Incoterm",
  moq: "MOQ",
  specification: "CoA/TDS",
};

const collectionStatus = (item: SupplierConversationRead) => {
  if (item.data_collection_status === "complete") {
    return { label: "Данные собраны", tone: "tone-ok" };
  }
  if (item.data_collection_status === "needs_human") {
    return { label: "Нужен человек", tone: "tone-warn" };
  }
  if (item.data_collection_status === "collecting") {
    const fields = item.missing_quote_fields
      .map((field) => QUOTE_FIELD_LABELS[field] ?? field)
      .join(", ");
    return {
      label: fields ? `Не хватает: ${fields}` : "Сбор данных",
      tone: "tone-info",
    };
  }
  return null;
};

const createActionId = () => {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

const formatMoment = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

const deliveryStatusLabel = (status: string) =>
  ({
    demo: "демонстрация",
    draft: "черновик",
    sending: "отправляется",
    sent: "отправлено",
    received: "получено",
    delivery_error: "доставка не подтверждена",
  })[status] ?? status;

const ATTACHMENT_STATUS_LABELS: Record<string, string> = {
  stored: "сохранён",
  extracted: "текст извлечён",
  ocr_extracted: "текст распознан",
  needs_ocr: "нужна ручная проверка",
  unsupported: "формат не поддерживается",
  rejected: "файл отклонён",
  failed: "не удалось сохранить",
  skipped: "файл пропущен",
};

const ATTACHMENT_KIND_LABELS: Record<string, string> = {
  coa: "CoA",
  tds: "TDS",
  msds: "SDS",
  other: "файл",
};

const formatAttachmentSize = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(bytes / 1024))} КБ`;

export default function DispatchTab({
  rfq,
  onStatusChanged,
  onGoToSuppliers,
}: {
  rfq: RFQRead;
  onStatusChanged: () => void;
  onGoToSuppliers: () => void;
}) {
  const rfqId = rfq.id;
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";
  const canTestCommunication = user?.role === "admin";

  const [overview, setOverview] = useState<CommunicationOverviewRead>(EMPTY_OVERVIEW);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [escalationBusy, setEscalationBusy] = useState<number | null>(null);
  const [messageBody, setMessageBody] = useState("");
  const [messageActionId, setMessageActionId] = useState(createActionId);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [draftBusy, setDraftBusy] = useState<number | null>(null);
  const [downloadBusy, setDownloadBusy] = useState<number | null>(null);
  const [dialogueTranslations, setDialogueTranslations] = useState<
    Record<number, string>
  >({});
  const [translationRevealed, setTranslationRevealed] = useState(false);
  const [translationBusy, setTranslationBusy] = useState(false);

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

  useEffect(() => {
    // Текст одного поставщика нельзя случайно перенести в другой диалог.
    setMessageBody("");
    setMessageActionId(createActionId());
  }, [selectedKey]);

  const selectedConversation =
    overview.conversations.find((item) => conversationKey(item) === selectedKey) ??
    null;
  const selectedMessageSignature =
    selectedConversation?.messages.map((message) => message.id).join(",") ?? "";

  useEffect(() => {
    setDialogueTranslations({});
    setTranslationRevealed(false);
  }, [selectedKey, selectedMessageSignature]);

  const toggleDialogueTranslation = async () => {
    if (translationRevealed) {
      setTranslationRevealed(false);
      return;
    }
    if (!selectedConversation) return;
    const messageIds = selectedConversation.messages
      .filter((message) => message.body?.trim())
      .map((message) => message.id);
    if (messageIds.length === 0) return;

    setTranslationBusy(true);
    setError(null);
    try {
      const result = await api.translateCommunicationDialogue(rfqId, messageIds);
      setDialogueTranslations(
        Object.fromEntries(
          result.translations.map((item) => [item.message_id, item.translation_ru]),
        ),
      );
      setTranslationRevealed(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTranslationBusy(false);
    }
  };
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

  const sendMessage = async () => {
    if (
      !selectedConversation?.manager_id ||
      !selectedConversation.contact ||
      !messageBody.trim()
    ) {
      return;
    }
    const channelLabel =
      selectedConversation.channel === "email" ? "Email" : "WhatsApp";
    if (
      !window.confirm(
        `Реально отправить сообщение через ${channelLabel} контакту ${selectedConversation.contact}?`,
      )
    ) {
      return;
    }
    setSendingMessage(true);
    setError(null);
    setNotice(null);
    try {
      await api.sendCommunicationMessage(rfqId, {
        manager_id: selectedConversation.manager_id,
        channel: selectedConversation.channel,
        body: messageBody.trim(),
        idempotency_key: messageActionId,
        confirm_external_send: true,
      });
      setMessageBody("");
      setMessageActionId(createActionId());
      setNotice(`Сообщение отправлено через ${channelLabel}.`);
      await load();
      onStatusChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await load();
    } finally {
      setSendingMessage(false);
    }
  };

  const sendDraft = async (communicationId: number) => {
    if (!window.confirm("Реально отправить этот Email-черновик поставщику?")) {
      return;
    }
    setDraftBusy(communicationId);
    setError(null);
    setNotice(null);
    try {
      await api.sendCommunicationDraft(communicationId);
      setNotice("Email-черновик отправлен.");
      await load();
      onStatusChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await load();
    } finally {
      setDraftBusy(null);
    }
  };

  const downloadAttachment = async (attachment: CommunicationAttachmentRead) => {
    if (!attachment.document_id) return;
    setDownloadBusy(attachment.document_id);
    setError(null);
    try {
      const blob = await api.downloadDocument(attachment.document_id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = attachment.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDownloadBusy(null);
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
      <RfqDispatchPreparation
        rfq={rfq}
        readOnly={readOnly}
        onGoToSuppliers={onGoToSuppliers}
        onChanged={async () => {
          await load();
          onStatusChanged();
        }}
      />

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

        {canTestCommunication && (
          <details className="conversation-test-dialog">
            <summary>
              <span>
                <strong>Тестовый поставщик</strong>
                <small>
                  Ответьте на RFQ сами или выберите полный/неполный пример
                </small>
              </span>
              <span className="badge tone-info">Тестовый режим</span>
            </summary>
            <CommunicationTesting embedded rfq={rfq} />
          </details>
        )}

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
                const progress = collectionStatus(item);
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
                    {openCount === 0 && progress && (
                      <span className={`badge ${progress.tone}`}>
                        {progress.label}
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
                    <div className="stack-inline">
                      {selectedConversation.messages.some((message) =>
                        message.body?.trim(),
                      ) && (
                        <button
                          className="secondary btn-small"
                          disabled={translationBusy}
                          onClick={() => void toggleDialogueTranslation()}
                          type="button"
                        >
                          {translationBusy
                            ? "Переводим…"
                            : translationRevealed
                              ? "Скрыть перевод диалога"
                              : "Перевести диалог"}
                        </button>
                      )}
                      {collectionStatus(selectedConversation) && (
                        <span
                          className={`badge ${
                            collectionStatus(selectedConversation)?.tone
                          }`}
                        >
                          {collectionStatus(selectedConversation)?.label}
                        </span>
                      )}
                      <span className="badge tone-neutral">
                        {selectedConversation.channel === "email"
                          ? "Email"
                          : "WhatsApp"}
                      </span>
                    </div>
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
                          <p>
                            {translationRevealed && dialogueTranslations[message.id]
                              ? dialogueTranslations[message.id]
                              : message.body || "—"}
                          </p>
                          {message.channel === "email" &&
                            message.attachments &&
                            message.attachments.length > 0 && (
                              <div
                                className="conversation-attachments"
                                aria-label="Вложения письма"
                              >
                                {message.attachments.map((attachment, index) => {
                                  const downloadable = attachment.document_id !== null;
                                  return (
                                    <div
                                      className={`conversation-attachment ${
                                        downloadable ? "" : "unavailable"
                                      }`}
                                      key={`${message.id}-${attachment.document_id ?? index}`}
                                    >
                                      <div>
                                        <strong>{attachment.filename}</strong>
                                        <span>
                                          {ATTACHMENT_KIND_LABELS[
                                            attachment.kind ?? "other"
                                          ] ?? "файл"}
                                          {` · ${formatAttachmentSize(attachment.size)}`}
                                          {attachment.page_count
                                            ? ` · ${attachment.page_count} стр.`
                                            : ""}
                                        </span>
                                        <span>
                                          {ATTACHMENT_STATUS_LABELS[attachment.status] ??
                                            attachment.status}
                                          {attachment.error
                                            ? `: ${attachment.error}`
                                            : ""}
                                        </span>
                                      </div>
                                      {downloadable && (
                                        <button
                                          className="secondary btn-small"
                                          disabled={
                                            downloadBusy === attachment.document_id
                                          }
                                          onClick={() =>
                                            void downloadAttachment(attachment)
                                          }
                                          type="button"
                                        >
                                          {downloadBusy === attachment.document_id
                                            ? "Скачивание…"
                                            : "Скачать"}
                                        </button>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          <div className="conversation-message-actions">
                            {message.status && (
                              <span className="conversation-delivery-status">
                                {deliveryStatusLabel(message.status)}
                              </span>
                            )}
                            {!readOnly &&
                              message.channel === "email" &&
                              message.direction === "outbound" &&
                              message.status === "draft" && (
                                <button
                                  className="secondary btn-small"
                                  disabled={draftBusy !== null}
                                  onClick={() => void sendDraft(message.id)}
                                  type="button"
                                >
                                  {draftBusy === message.id
                                    ? "Отправка…"
                                    : "Отправить черновик"}
                                </button>
                              )}
                          </div>
                        </article>
                      ))
                    )}
                  </div>

                  {!readOnly && (
                    <div className="conversation-composer">
                      {selectedConversation.manager_id &&
                      selectedConversation.contact ? (
                        <>
                          <Textarea
                            rows={4}
                            placeholder="Напишите сообщение поставщику"
                            value={messageBody}
                            onChange={(event) => {
                              setMessageBody(event.target.value);
                              setMessageActionId(createActionId());
                            }}
                          />
                          <div className="conversation-composer-footer">
                            <span className="note">
                              {selectedConversation.channel === "email"
                                ? "Будет отправлено через подключённый SMTP."
                                : "Будет отправлено через Meta WhatsApp Cloud API в открытом 24-часовом окне."}
                            </span>
                            <button
                              disabled={sendingMessage || !messageBody.trim()}
                              onClick={() => void sendMessage()}
                              type="button"
                            >
                              {sendingMessage ? "Отправка…" : "Отправить"}
                            </button>
                          </div>
                        </>
                      ) : (
                        <p className="note">
                          Для ответа свяжите диалог с контактом поставщика.
                        </p>
                      )}
                    </div>
                  )}
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
