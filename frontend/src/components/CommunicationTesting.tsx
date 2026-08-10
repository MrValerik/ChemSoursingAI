import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CommunicationTestRun, RFQRead } from "../api/types";
import { Field, Input, Select, Textarea } from "./ui";

const STATUS_LABELS: Record<string, string> = {
  classifying: "Проверка ответа поставщика",
  generating: "Нейросеть формирует сообщение",
  previewed: "Диалог активен",
  sending: "Отправка сообщения",
  sent: "Сообщение отправлено",
  escalated: "Требуется ответ человека",
  llm_error: "Ошибка нейросети",
  delivery_error: "Ошибка доставки",
  processing_error: "Ошибка обработки",
};

const STATUS_TONES: Record<string, string> = {
  classifying: "tone-info",
  generating: "tone-info",
  previewed: "tone-ok",
  sending: "tone-info",
  sent: "tone-ok",
  escalated: "tone-warn",
  llm_error: "tone-warn",
  delivery_error: "tone-warn",
  processing_error: "tone-warn",
};

const procurementContextFromRfq = (rfq: RFQRead) =>
  [
    `Вещество: ${rfq.name}`,
    rfq.cas ? `CAS: ${rfq.cas}` : null,
    rfq.purity ? `Чистота или грейд: ${rfq.purity}` : null,
    rfq.volume ? `Количество: ${rfq.volume}` : null,
    rfq.specification ? `Спецификация: ${rfq.specification}` : null,
    rfq.application ? `Применение: ${rfq.application}` : null,
    rfq.incoterms?.length
      ? `Базисы поставки: ${rfq.incoterms.join(", ")}`
      : null,
  ]
    .filter(Boolean)
    .join("\n");

export default function CommunicationTesting({
  embedded = false,
  rfq,
}: {
  embedded?: boolean;
  rfq?: RFQRead;
} = {}) {
  const [channel, setChannel] = useState<"email" | "whatsapp">("email");
  const [recipient, setRecipient] = useState("");
  const [procurementContext, setProcurementContext] = useState(() =>
    rfq ? procurementContextFromRfq(rfq) : "",
  );
  const [supplierMessage, setSupplierMessage] = useState("");
  const [instructions, setInstructions] = useState("");
  const [subject, setSubject] = useState(
    rfq?.rfq_subject ?? "Request for quotation",
  );
  const [deliveryMode, setDeliveryMode] = useState<"preview" | "send">(
    "preview",
  );
  const [history, setHistory] = useState<CommunicationTestRun[]>([]);
  const [active, setActive] = useState<CommunicationTestRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = async () => {
    const items = await api.listCommunicationTests();
    setHistory(items);
    setActive((current) =>
      current ? (items.find((item) => item.id === current.id) ?? current) : current,
    );
  };

  useEffect(() => {
    if (embedded) return;
    loadHistory().catch((reason) => setError(String(reason)));
    const interval = window.setInterval(() => {
      loadHistory().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [embedded]);

  useEffect(() => {
    if (!rfq) return;
    setProcurementContext(procurementContextFromRfq(rfq));
    setSubject(rfq.rfq_subject ?? "Request for quotation");
    setDeliveryMode("preview");
    setRecipient("");
    setActive(null);
    setSupplierMessage("");
    setError(null);
  }, [rfq]);

  const startDialog = async () => {
    if (!procurementContext.trim()) return;
    const live = deliveryMode === "send";
    if (!recipient.trim() && live) return;
    if (
      live &&
      !window.confirm(
        `Нейросеть сформирует первое сообщение и реально отправит его через ${
          channel === "email" ? "Email" : "WhatsApp"
        } указанному получателю. Продолжить?`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.runCommunicationTest({
        channel,
        recipient: recipient.trim(),
        procurement_context: procurementContext.trim(),
        additional_instructions: instructions.trim(),
        delivery_mode: deliveryMode,
        subject: subject.trim() || "Request for quotation",
        confirm_external_send: live,
      });
      setActive(created);
      setSupplierMessage("");
      if (!embedded) await loadHistory();
    } catch (reason) {
      setError(String(reason));
      if (!embedded) await loadHistory().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const continueDialog = async () => {
    if (!active || !supplierMessage.trim()) return;
    const live = active.delivery_mode === "send";
    if (!recipient.trim() && live) return;
    if (
      live &&
      !window.confirm(
        "Сохранить введённый ответ как сообщение поставщика и реально отправить следующий ответ нейросети?",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.continueCommunicationTest(active.id, {
        supplier_message: supplierMessage.trim(),
        recipient: recipient.trim(),
        confirm_external_send: live,
      });
      setActive(updated);
      setSupplierMessage("");
      if (!embedded) await loadHistory();
    } catch (reason) {
      setError(String(reason));
      if (!embedded) await loadHistory().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const openDialog = (item: CommunicationTestRun) => {
    setActive(item);
    setChannel(item.channel);
    setProcurementContext(item.procurement_context);
    setInstructions(item.additional_instructions ?? "");
    setSubject(item.subject);
    setDeliveryMode(item.delivery_mode);
    setRecipient("");
    setSupplierMessage("");
    setError(null);
  };

  const canContinue =
    active !== null &&
    active.messages.length > 0 &&
    active.messages[active.messages.length - 1].sender_role === "assistant" &&
    active.status !== "delivery_error";

  return (
    <div
      className={
        embedded
          ? "communication-testing communication-testing-embedded"
          : "requests-page communication-testing"
      }
    >
      <div className="requests-header">
        <div>
          {embedded ? <h2>Тестирование общения</h2> : <h1>Тестирование общения</h1>}
          <p className="note">
            Администраторская песочница: {embedded
              ? "данные текущего запроса уже подставлены, "
              : "задайте потребность, "}
            выделенная облачная
            нейросеть первой обратится к поставщику, а затем будет отвечать на
            ваши тестовые реплики с учётом всей истории. Фактически использованная
            модель отображается под диалогом. Внешняя переписка ведётся на
            английском, а сотруднику показывается русский перевод.
          </p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="communication-test-layout">
        <div className="panel">
          <h2>Новый диалог</h2>
          <div className="settings-grid">
            <Field label="Канал">
              <Select
                value={channel}
                onChange={(next) => setChannel(next as "email" | "whatsapp")}
                options={[
                  { value: "email", label: "Email" },
                  { value: "whatsapp", label: "WhatsApp" },
                ]}
              />
            </Field>
            {!embedded && (
              <Field
                label={channel === "email" ? "Email получателя" : "Номер WhatsApp"}
                hint={
                  deliveryMode === "preview"
                    ? "Для симуляции получателя можно не указывать"
                    : "Обязателен для реальной отправки"
                }
              >
                <Input
                  placeholder={
                    channel === "email" ? "test@example.com" : "+7 900 000-00-00"
                  }
                  type={channel === "email" ? "email" : "tel"}
                  value={recipient}
                  onChange={(event) => setRecipient(event.target.value)}
                />
              </Field>
            )}
            <Field label="Язык переговоров">
              <Input disabled value="Английский · русский перевод в интерфейсе" />
            </Field>
            {!embedded && (
              <Field label="Режим">
                <Select
                  value={deliveryMode}
                  onChange={(next) => setDeliveryMode(next as "preview" | "send")}
                  options={[
                    { value: "preview", label: "Только симуляция" },
                    { value: "send", label: "Генерировать и отправлять реально" },
                  ]}
                />
              </Field>
            )}
            {embedded && (
              <Field label="Режим">
                <Input disabled value="Только симуляция · без внешней отправки" />
              </Field>
            )}
          </div>
          {channel === "email" && (
            <Field label="Тема тестового письма">
              <Input
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
              />
            </Field>
          )}
          <Field
            label="Общая информация о закупке"
            hint="Достаточно свободного описания вещества, количества и известных требований"
          >
            <Textarea
              rows={6}
              placeholder="Например: 50 кг аммиака. Нужна цена, срок поставки и CoA."
              value={procurementContext}
              onChange={(event) => setProcurementContext(event.target.value)}
            />
          </Field>
          <Field
            label="Дополнительные инструкции"
            hint="Можно уточнить тон и цель; ограничения безопасности изменить нельзя"
          >
            <Textarea
              rows={3}
              placeholder="Например: писать очень кратко"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
            />
          </Field>
          {deliveryMode === "send" && channel === "email" && (
            <p className="external-action-warning">
              Первое письмо будет отправлено после подтверждения. Ответы на него
              проверяются автоматически: по обычным условиям закупки нейросеть
              продолжит Email-цепочку, а нестандартный вопрос передаст человеку.
            </p>
          )}
          {deliveryMode === "send" && channel === "whatsapp" && (
            <p className="external-action-warning">
              Это реальное внешнее действие. Каждая отправка потребует отдельного
              подтверждения.
            </p>
          )}
          <div className="actions">
            <button
              disabled={
                busy ||
                !procurementContext.trim() ||
                (deliveryMode === "send" && !recipient.trim())
              }
              onClick={() => void startDialog()}
            >
              {busy
                ? "Нейросеть пишет…"
                : deliveryMode === "send"
                  ? "Начать и отправить"
                  : "Начать диалог"}
            </button>
          </div>
        </div>

        <div className="panel communication-dialog-panel">
          <h2>Диалог</h2>
          {active ? (
            <>
              <div className="communication-context">
                <strong>Контекст:</strong> {active.procurement_context}
              </div>
              <div className="communication-messages">
                {active.messages.map((message) => (
                  <div
                    className={`communication-message ${
                      message.sender_role === "assistant"
                        ? "from-assistant"
                        : "from-supplier"
                    }`}
                    key={message.id}
                  >
                    <span className="communication-message-role">
                      {message.sender_role === "assistant"
                        ? "Нейросеть · покупатель"
                        : active.channel === "email" &&
                            active.delivery_mode === "send"
                          ? "Поставщик · Email"
                          : "Вы · поставщик"}
                    </span>
                    <div className="communication-message-original">
                      <span>
                        {message.sender_role === "assistant"
                          ? "Английский оригинал"
                          : "Оригинал поставщика"}
                      </span>
                      <div>{message.content}</div>
                    </div>
                    {message.translation_ru &&
                      message.translation_ru !== message.content && (
                        <div className="communication-message-translation">
                          <span>Перевод для сотрудника</span>
                          <div>{message.translation_ru}</div>
                        </div>
                      )}
                    {!message.translation_ru && (
                      <div className="communication-message-translation unavailable">
                        Перевод временно недоступен
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <p className="note">
                {STATUS_LABELS[active.status] ?? active.status}
                {active.model ? ` · модель: ${active.model}` : ""}
              </p>
              {active.error && <p className="error">{active.error}</p>}
              {canContinue && (
                <div className="communication-reply">
                  <Field label="Ответ поставщика (оригинал)">
                    <Textarea
                      rows={4}
                      placeholder="Введите английский ответ поставщика — нейросеть продолжит диалог"
                      value={supplierMessage}
                      onChange={(event) => setSupplierMessage(event.target.value)}
                    />
                  </Field>
                  <button
                    disabled={
                      busy ||
                      !supplierMessage.trim() ||
                      (active.delivery_mode === "send" && !recipient.trim())
                    }
                    onClick={() => void continueDialog()}
                  >
                    {busy ? "Нейросеть отвечает…" : "Ответить и продолжить"}
                  </button>
                </div>
              )}
            </>
          ) : (
            <p className="empty">
              Укажите общую информацию и начните диалог. Первое сообщение
              сформирует нейросеть.
            </p>
          )}
        </div>
      </div>

      {!embedded && (
        <div className="panel">
          <h2>Последние диалоги</h2>
          {history.length === 0 ? (
            <p className="empty">Диалоги ещё не запускались.</p>
          ) : (
            <table className="summary">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Канал</th>
                  <th>Получатель</th>
                  <th>Режим</th>
                  <th>Статус</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {history.map((item) => (
                  <tr key={item.id}>
                    <td>{new Date(item.created_at).toLocaleString("ru-RU")}</td>
                    <td>{item.channel === "email" ? "Email" : "WhatsApp"}</td>
                    <td>{item.recipient_masked}</td>
                    <td>
                      {item.delivery_mode === "send" ? "Отправка" : "Симуляция"}
                    </td>
                    <td>
                      <span
                        className={`badge ${STATUS_TONES[item.status] ?? "tone-neutral"}`}
                      >
                        {STATUS_LABELS[item.status] ?? item.status}
                      </span>
                    </td>
                    <td>
                      <button
                        className="secondary btn-small"
                        onClick={() => openDialog(item)}
                      >
                        Открыть
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
