// Вкладка «Общение»: история диалогов с поставщиками и очередь эскалаций.

import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  CommunicationAttachmentRead,
  CommunicationEscalationRead,
  CommunicationOverviewRead,
  CommunicationProfile,
  CommunicationProfileStatus,
  CommunicationTestRun,
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

const testConversationKey = (runId: number) => `test:${runId}`;
const NEW_TEST_CONVERSATION_KEY = "test:new";
const MAX_CHAT_FILES = 5;
const MAX_CHAT_FILE_BYTES = 25 * 1024 * 1024;
const CHAT_FILE_ACCEPT =
  ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.txt,.csv,.xlsx,.xls,.docx,.doc";

const TEST_STATUS_LABELS: Record<string, string> = {
  previewed: "Диалог активен",
  escalated: "Нужен человек",
  complete: "Данные собраны",
  llm_error: "Ошибка нейросети",
  processing_error: "Ошибка обработки",
};

const QUOTE_FIELD_LABELS: Record<string, string> = {
  price: "цена",
  incoterm: "Incoterm",
  moq: "MOQ",
  currency: "валюта",
  grade: "грейд / чистота",
  payment_terms: "условия оплаты",
  lead_time: "срок производства и доставки",
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
  sent_file: "отправлен",
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
  compact = false,
  focusedSupplierId = null,
  focusedManagerId = null,
  focusedTestRunId = null,
  focusedChannel = null,
}: {
  rfq: RFQRead;
  onStatusChanged: () => void;
  onGoToSuppliers?: () => void;
  compact?: boolean;
  focusedSupplierId?: number | null;
  focusedManagerId?: number | null;
  focusedTestRunId?: number | null;
  focusedChannel?: "email" | "whatsapp" | null;
}) {
  const rfqId = rfq.id;
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";
  const canTestCommunication =
    user?.role === "admin" || user?.role === "buyer";

  const [overview, setOverview] = useState<CommunicationOverviewRead>(EMPTY_OVERVIEW);
  const [testRuns, setTestRuns] = useState<CommunicationTestRun[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [escalationBusy, setEscalationBusy] = useState<number | null>(null);
  const [messageBody, setMessageBody] = useState("");
  const [messageFiles, setMessageFiles] = useState<File[]>([]);
  const messageFileInputRef = useRef<HTMLInputElement>(null);
  const [messageActionId, setMessageActionId] = useState(createActionId);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [draftBusy, setDraftBusy] = useState<number | null>(null);
  const [downloadBusy, setDownloadBusy] = useState<number | null>(null);
  const [dialogueTranslations, setDialogueTranslations] = useState<
    Record<number, string>
  >({});
  const [translationRevealed, setTranslationRevealed] = useState(false);
  const [translationBusy, setTranslationBusy] = useState(false);
  const [profiles, setProfiles] = useState<CommunicationProfile[]>([]);
  const [profileStatus, setProfileStatus] =
    useState<CommunicationProfileStatus | null>(null);
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const canSyncEmail =
    user?.role === "buyer" || user?.role === "head" || user?.role === "admin";
  const focusSignature = [
    focusedSupplierId ?? "",
    focusedManagerId ?? "",
    focusedTestRunId ?? "",
    focusedChannel ?? "",
  ].join(":");

  const load = async () => {
    try {
      const [communicationItems, testItems] = await Promise.all([
        api.communicationOverview(rfqId),
        canTestCommunication
          ? api.listCommunicationTests(100, rfqId)
          : Promise.resolve([] as CommunicationTestRun[]),
      ]);
      try {
        const [profileItems, currentProfileStatus] = await Promise.all([
          api.listCommunicationProfiles(),
          api.communicationProfileStatus(rfqId),
        ]);
        setProfiles(profileItems.filter((item) => item.is_active));
        setProfileStatus(currentProfileStatus);
        setProfileError(null);
      } catch (caught) {
        setProfileStatus(null);
        setProfileError(
          caught instanceof Error ? caught.message : String(caught),
        );
      }
      const embeddedTests = testItems.filter(
        (item) =>
          item.simulation_mode === "buyer_ai" && item.delivery_mode === "preview",
      );
      setOverview(communicationItems);
      setTestRuns(embeddedTests);
      setSelectedKey((current) => {
        const focusedConversation =
          communicationItems.conversations.find(
            (item) =>
              focusedManagerId !== null &&
              item.manager_id === focusedManagerId &&
              (focusedChannel === null || item.channel === focusedChannel),
          ) ??
          communicationItems.conversations.find(
            (item) =>
              focusedManagerId !== null && item.manager_id === focusedManagerId,
          ) ??
          communicationItems.conversations.find(
            (item) =>
              focusedSupplierId !== null &&
              item.supplier_id === focusedSupplierId &&
              (focusedChannel === null || item.channel === focusedChannel),
          ) ??
          communicationItems.conversations.find(
            (item) =>
              focusedSupplierId !== null && item.supplier_id === focusedSupplierId,
          );
        const focusedTestKey =
          focusedTestRunId !== null &&
          embeddedTests.some((item) => item.id === focusedTestRunId)
            ? testConversationKey(focusedTestRunId)
            : null;
        const requestedKey =
          focusedTestKey ??
          (focusedConversation ? conversationKey(focusedConversation) : null);
        if (compact) return requestedKey;
        if (
          current &&
          (communicationItems.conversations.some(
            (item) => conversationKey(item) === current,
          ) ||
            embeddedTests.some(
              (item) => testConversationKey(item.id) === current,
            ) ||
            (canTestCommunication && current === NEW_TEST_CONVERSATION_KEY))
        ) {
          return current;
        }
        if (communicationItems.conversations[0]) {
          return conversationKey(communicationItems.conversations[0]);
        }
        if (embeddedTests[0]) return testConversationKey(embeddedTests[0].id);
        return canTestCommunication ? NEW_TEST_CONVERSATION_KEY : null;
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
  }, [rfqId, canTestCommunication, compact, focusSignature]);

  useEffect(() => {
    // Текст одного поставщика нельзя случайно перенести в другой диалог.
    setMessageBody("");
    setMessageFiles([]);
    setMessageActionId(createActionId());
  }, [selectedKey]);

  const selectedConversation =
    overview.conversations.find((item) => conversationKey(item) === selectedKey) ??
    null;
  const selectedTestRunId = selectedKey?.startsWith("test:")
    ? Number(selectedKey.slice("test:".length)) || null
    : undefined;
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
          `Связано новых адресов: ${result.contacts_linked}. ` +
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
      (!messageBody.trim() && messageFiles.length === 0)
    ) {
      return;
    }
    const channelLabel =
      selectedConversation.channel === "email" ? "Email" : "WhatsApp";
    if (
      !window.confirm(
        `Реально отправить сообщение через ${channelLabel} контакту ${selectedConversation.contact}` +
          (messageFiles.length > 0
            ? ` и приложить файлов: ${messageFiles.length}?`
            : "?"),
      )
    ) {
      return;
    }
    setSendingMessage(true);
    setError(null);
    setNotice(null);
    try {
      const payload = {
        manager_id: selectedConversation.manager_id,
        channel: selectedConversation.channel,
        body: messageBody.trim(),
        idempotency_key: messageActionId,
        confirm_external_send: true,
      };
      if (messageFiles.length > 0) {
        await api.sendCommunicationMessageWithAttachments(rfqId, {
          ...payload,
          files: messageFiles,
        });
      } else {
        await api.sendCommunicationMessage(rfqId, payload);
      }
      setMessageBody("");
      setMessageFiles([]);
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

  const selectMessageFiles = (files: FileList | null) => {
    const incoming = Array.from(files ?? []);
    if (incoming.length === 0) return;
    const next = [...messageFiles, ...incoming];
    if (next.length > MAX_CHAT_FILES) {
      setError(
        `К одному сообщению можно прикрепить не больше ${MAX_CHAT_FILES} файлов.`,
      );
      return;
    }
    const oversized = incoming.find((file) => file.size > MAX_CHAT_FILE_BYTES);
    if (oversized) {
      setError(`Файл ${oversized.name} больше 25 МБ.`);
      return;
    }
    setMessageFiles(next);
    setMessageActionId(createActionId());
    setError(null);
  };

  const removeMessageFile = (index: number) => {
    setMessageFiles((current) =>
      current.filter((_, itemIndex) => itemIndex !== index),
    );
    setMessageActionId(createActionId());
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

  const replyToEscalation = async (
    escalation: CommunicationEscalationRead,
    conversation: SupplierConversationRead,
    body: string,
    idempotencyKey: string,
  ): Promise<boolean> => {
    if (
      !user ||
      !conversation.manager_id ||
      !conversation.contact ||
      !body.trim()
    ) {
      return false;
    }
    const channelLabel = conversation.channel === "email" ? "Email" : "WhatsApp";
    if (
      !window.confirm(
        `Реально отправить ручной ответ через ${channelLabel} контакту ${conversation.contact} и закрыть эскалацию?`,
      )
    ) {
      return false;
    }

    setEscalationBusy(escalation.id);
    setError(null);
    setNotice(null);
    try {
      await api.sendCommunicationMessage(rfqId, {
        manager_id: conversation.manager_id,
        channel: conversation.channel,
        body: body.trim(),
        idempotency_key: idempotencyKey,
        confirm_external_send: true,
      });
      await api.updateEscalation(escalation.id, {
        assignee: escalation.assignee ?? user.full_name,
        status: "resolved",
      });
      setNotice(`Ответ отправлен через ${channelLabel}, эскалация закрыта.`);
      await load();
      onStatusChanged();
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await load();
      return false;
    } finally {
      setEscalationBusy(null);
    }
  };

  const updateTestRun = (run: CommunicationTestRun) => {
    setTestRuns((current) => [
      run,
      ...current.filter((item) => item.id !== run.id),
    ]);
    setSelectedKey(testConversationKey(run.id));
    onStatusChanged();
  };

  const assignProfile = async (profileId: number | null) => {
    setProfileBusy(true);
    setError(null);
    try {
      await api.assignCurrentUserCommunicationProfile(profileId);
      await load();
      setNotice("Ваш профиль общения обновлён.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setProfileBusy(false);
    }
  };

  return (
    <div>
      {!compact && (
        <RfqDispatchPreparation
          rfq={rfq}
          readOnly={readOnly}
          onGoToSuppliers={onGoToSuppliers ?? (() => undefined)}
          onChanged={async () => {
            await load();
            onStatusChanged();
          }}
        />
      )}

      <div
        className={`panel communication-panel ${
          compact ? "summary-dialogue-panel" : ""
        }`}
      >
        <div className="tab-toolbar">
          <div>
            <h2>{compact ? "Диалог с поставщиком" : "Диалоги с поставщиками"}</h2>
            <p className="note">
              {compact
                ? "Переписка, из которой получены условия выбранного предложения."
                : "Входящие сообщения, ответы и вопросы, переданные сотруднику."}
            </p>
          </div>
          {!compact && (
            <div className="communication-toolbar-actions">
              <button
                aria-expanded={settingsOpen}
                className="secondary"
                onClick={() => setSettingsOpen((current) => !current)}
                type="button"
              >
                {settingsOpen ? "Закрыть настройки" : "Настройки общения"}
              </button>
              {activeEscalations.length > 0 && (
                <span className="badge tone-warn" role="status">
                  Требуют ответа: {activeEscalations.length}
                </span>
              )}
              {canTestCommunication && (
                <button
                  className="secondary"
                  onClick={() => setSelectedKey(NEW_TEST_CONVERSATION_KEY)}
                  type="button"
                >
                  Начать новый тестовый диалог
                </button>
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
          )}
        </div>

        {!compact && settingsOpen && (
          <section className="settings-accordion-body communication-settings">
            {profileStatus ? (
              <>
                <div className="tab-toolbar">
                  <div>
                    <h3>Профиль пользователя</h3>
                    <p className="note">
                      {profileStatus.user_name} · {profileStatus.profile_name} · v
                      {profileStatus.profile_version} · {profileStatus.source === "user"
                        ? "выбран пользователем"
                        : "системный по умолчанию"}
                    </p>
                  </div>
                  <select
                    aria-label="Мой профиль общения"
                    disabled={profileBusy || readOnly}
                    value={profileStatus.source === "user" ? profileStatus.profile_id : ""}
                    onChange={(event) =>
                      void assignProfile(event.target.value ? Number(event.target.value) : null)
                    }
                  >
                    <option value="">Закупщик по умолчанию</option>
                    {profiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.name} · v{profile.version}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="note">
                  Выбор и расход лимитов относятся только к вашей учётной записи.
                </p>
                <div className="stack-inline">
                  <span className={`badge ${profileStatus.stopped ? "tone-warn" : "tone-ok"}`}>
                    {profileStatus.stopped ? "Автоответы остановлены" : "Автоответы разрешены"}
                  </span>
                  <span className="badge tone-neutral">
                    Ответы: {profileStatus.budget.automatic_replies_used} / {profileStatus.budget.max_auto_replies}
                  </span>
                  <span className="badge tone-neutral">
                    Токены: {profileStatus.budget.prompt_tokens_used + profileStatus.budget.completion_tokens_used} / {profileStatus.budget.max_prompt_tokens + profileStatus.budget.max_completion_tokens}
                  </span>
                  <span className="badge tone-neutral">
                    Время: {Math.ceil(profileStatus.budget.elapsed_seconds / 3600)} / {Math.ceil(profileStatus.budget.max_duration_seconds / 3600)} ч
                  </span>
                  <span className="badge tone-neutral">
                    Расход: {profileStatus.budget.estimated_cost_rub.toFixed(2)} / {profileStatus.budget.max_estimated_cost_rub.toFixed(2)} ₽
                  </span>
                </div>
                {profileStatus.stopped && <p className="error">{profileStatus.explanation}</p>}
              </>
            ) : (
              <div>
                <h3>Профиль пользователя</h3>
                <p className={profileError ? "error" : "note"}>
                  {profileError
                    ? `Не удалось загрузить настройки: ${profileError}`
                    : "Настройки профиля загружаются…"}
                </p>
              </div>
            )}
          </section>
        )}

        {notice && <p className="success-note">{notice}</p>}
        {error && <p className="error">{error}</p>}

        {!compact &&
          overview.unassigned_escalations
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

        {!compact &&
        overview.conversations.length === 0 &&
        !canTestCommunication ? (
          <p className="note">
            Диалогов пока нет. После отправки запроса или получения ответа
            поставщик появится здесь.
          </p>
        ) : (
          <div
            className={`conversation-layout ${
              compact ? "summary-conversation-layout" : ""
            }`}
          >
            {!compact && (
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
                {canTestCommunication &&
                  testRuns.map((run) => {
                  const lastMessage =
                    run.messages[run.messages.length - 1]?.content ??
                    "Сообщений пока нет";
                  const tone =
                    run.status === "escalated"
                      ? "tone-warn"
                      : run.status === "complete"
                        ? "tone-ok"
                        : "tone-info";
                  return (
                    <button
                      className={`conversation-supplier ${
                        selectedKey === testConversationKey(run.id) ? "active" : ""
                      }`}
                      key={testConversationKey(run.id)}
                      onClick={() => setSelectedKey(testConversationKey(run.id))}
                      type="button"
                    >
                      <span className="conversation-supplier-title">
                        Тестовый поставщик
                      </span>
                      <span className="conversation-supplier-meta">
                        Тестовый диалог · {formatMoment(run.created_at)}
                      </span>
                      <span className="conversation-preview">{lastMessage}</span>
                      <span className={`badge ${tone}`}>
                        {TEST_STATUS_LABELS[run.status] ?? run.status}
                      </span>
                    </button>
                  );
                  })}
              </div>
            )}

            <div className="conversation-thread">
              {selectedTestRunId !== undefined && (
                <CommunicationTesting
                  embedded
                  rfq={rfq}
                  selectedRunId={selectedTestRunId}
                  onRunChanged={updateTestRun}
                />
              )}
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

                  {selectedConversation.channel === "email" &&
                    selectedConversation.linked_contacts.length > 1 && (
                      <p className="success-note">
                        Диалог объединён с почтой{" "}
                        {selectedConversation.linked_contacts[0]}. Связанный
                        адрес: {selectedConversation.linked_contacts
                          .slice(1)
                          .join(", ")}.
                      </p>
                    )}

                  {selectedConversation.escalations
                    .filter((item) => item.status !== "resolved")
                    .map((item) => (
                      <EscalationNotice
                        key={item.id}
                        escalation={item}
                        busy={escalationBusy === item.id}
                        readOnly={readOnly}
                        onAction={updateEscalation}
                        conversation={selectedConversation}
                        onReply={replyToEscalation}
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
                          <div className="conversation-composer-input">
                            <button
                              aria-label="Прикрепить файл"
                              className="conversation-attach-button secondary"
                              disabled={sendingMessage}
                              onClick={() => messageFileInputRef.current?.click()}
                              title="Прикрепить файл"
                              type="button"
                            >
                              <svg aria-hidden="true" viewBox="0 0 24 24">
                                <path d="M8.5 12.5 15.9 5a3.2 3.2 0 0 1 4.6 4.5L10.2 19.8a5 5 0 0 1-7.1-7.1L13 2.8" />
                              </svg>
                            </button>
                            <Textarea
                              rows={4}
                              placeholder="Напишите сообщение поставщику"
                              value={messageBody}
                              onChange={(event) => {
                                setMessageBody(event.target.value);
                                setMessageActionId(createActionId());
                              }}
                            />
                            <input
                              accept={CHAT_FILE_ACCEPT}
                              className="visually-hidden"
                              multiple
                              onChange={(event) => {
                                selectMessageFiles(event.target.files);
                                event.target.value = "";
                              }}
                              ref={messageFileInputRef}
                              type="file"
                            />
                          </div>
                          {messageFiles.length > 0 && (
                            <div className="conversation-selected-files">
                              {messageFiles.map((file, index) => (
                                <span
                                  className="badge tone-neutral"
                                  key={`${file.name}-${file.size}-${index}`}
                                >
                                  {file.name} · {formatAttachmentSize(file.size)}
                                  <button
                                    aria-label={`Убрать файл ${file.name}`}
                                    disabled={sendingMessage}
                                    onClick={() => removeMessageFile(index)}
                                    type="button"
                                  >
                                    ×
                                  </button>
                                </span>
                              ))}
                            </div>
                          )}
                          <div className="conversation-composer-footer">
                            <span className="note">
                              {selectedConversation.channel === "email"
                                ? "Будет отправлено через подключённую электронную почту."
                                : "Будет отправлено через подключённый канал WhatsApp."}
                            </span>
                            <button
                              disabled={
                                sendingMessage ||
                                (!messageBody.trim() && messageFiles.length === 0)
                              }
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
              {compact &&
                !selectedConversation &&
                selectedTestRunId === undefined && (
                  <p className="note">
                    Для этого предложения сохранённый диалог не найден.
                  </p>
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
  conversation,
  onReply,
}: {
  escalation: CommunicationEscalationRead;
  busy: boolean;
  readOnly: boolean;
  conversation?: SupplierConversationRead;
  onAction: (
    escalation: CommunicationEscalationRead,
    action: "take" | "resolve",
  ) => Promise<void>;
  onReply?: (
    escalation: CommunicationEscalationRead,
    conversation: SupplierConversationRead,
    body: string,
    idempotencyKey: string,
  ) => Promise<boolean>;
}) {
  const [replyBody, setReplyBody] = useState("");
  const [replyActionId, setReplyActionId] = useState(createActionId);
  const canReply = Boolean(
    conversation?.manager_id && conversation.contact && onReply,
  );

  const submitReply = async () => {
    if (!conversation || !onReply || !replyBody.trim()) return;
    const sent = await onReply(
      escalation,
      conversation,
      replyBody.trim(),
      replyActionId,
    );
    if (sent) {
      setReplyBody("");
      setReplyActionId(createActionId());
    }
  };

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
          {canReply && (
            <div className="communication-escalation-composer">
              <Textarea
                rows={3}
                placeholder="Напишите ручной ответ поставщику"
                value={replyBody}
                onChange={(event) => {
                  setReplyBody(event.target.value);
                  setReplyActionId(createActionId());
                }}
              />
              <button
                disabled={busy || !replyBody.trim()}
                onClick={() => void submitReply()}
                type="button"
              >
                {busy ? "Отправка…" : "Ответить поставщику"}
              </button>
            </div>
          )}
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
