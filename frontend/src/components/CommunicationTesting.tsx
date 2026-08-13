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
  complete: "Данные по котировке собраны",
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
  complete: "tone-ok",
};

const QUOTE_FIELD_LABELS: Record<string, string> = {
  price: "цена",
  incoterm: "Incoterm",
  moq: "MOQ",
  specification: "CoA/TDS",
};

const EXAMPLES = {
  complete: {
    title: "Полный ответ",
    supplierMessage:
      "USD 720/MT, MOQ: 100 kg, CIP Moscow. CoA attached.",
  },
  incomplete: {
    title: "Неполный ответ",
    supplierMessage: "Our price is USD 720 per MT, CIP Moscow.",
  },
} as const;

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

function DialogueTranslation({
  run,
  revealed,
  onRevealedChange,
  onTranslated,
}: {
  run: CommunicationTestRun;
  revealed: boolean;
  onRevealedChange: (revealed: boolean) => void;
  onTranslated: (run: CommunicationTestRun) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (revealed) {
      onRevealedChange(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const translated = await api.translateCommunicationTestDialogue(run.id);
      onTranslated(translated);
      onRevealedChange(true);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="communication-dialog-translation">
      <button
        className="secondary btn-small"
        disabled={busy}
        onClick={() => void toggle()}
        type="button"
      >
        {busy
          ? "Переводим…"
          : revealed
            ? "Скрыть перевод диалога"
            : "Перевести диалог"}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

function EmbeddedCommunicationTesting({ rfq }: { rfq: RFQRead }) {
  const [active, setActive] = useState<CommunicationTestRun | null>(null);
  const [supplierMessage, setSupplierMessage] = useState("");
  const [translationRevealed, setTranslationRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const context = procurementContextFromRfq(rfq);
  const rfqBody = rfq.rfq_body?.trim() ?? "";
  const canContinue =
    active !== null &&
    active.messages[active.messages.length - 1]?.sender_role === "assistant" &&
    !["delivery_error", "complete", "escalated"].includes(active.status);

  useEffect(() => {
    setActive(null);
    setSupplierMessage("");
    setTranslationRevealed(false);
    setError(null);
  }, [rfq.id, rfq.rfq_body]);

  const start = async () => {
    if (!rfqBody) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.runCommunicationTest({
        channel: "email",
        recipient: "",
        procurement_context: context,
        additional_instructions: "",
        simulation_mode: "buyer_ai",
        initial_message: rfqBody,
        delivery_mode: "preview",
        subject: rfq.rfq_subject?.trim() || "Request for quotation",
        confirm_external_send: false,
      });
      setActive(created);
      setSupplierMessage("");
      setTranslationRevealed(false);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const reply = async (message: string) => {
    if (!active || !canContinue || !message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.continueCommunicationTest(active.id, {
        message: message.trim(),
        recipient: "",
        confirm_external_send: false,
      });
      setActive(updated);
      setSupplierMessage("");
      setTranslationRevealed(false);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="communication-testing communication-testing-embedded">
      <div className="requests-header">
        <div>
          <h3>Диалог с тестовым поставщиком</h3>
          <p className="note">
            Первое сообщение — сохранённый RFQ текущего запроса. Сообщения никуда
            не отправляются.
          </p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {!active ? (
        <div className="embedded-test-start">
          <button disabled={busy || !rfqBody} onClick={() => void start()} type="button">
            {busy ? "Начинаем…" : "Начать диалог"}
          </button>
        </div>
      ) : (
        <div className="communication-dialog-panel embedded-test-thread">
          <DialogueTranslation
            run={active}
            revealed={translationRevealed}
            onRevealedChange={setTranslationRevealed}
            onTranslated={setActive}
          />
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
                    : "Вы · поставщик"}
                </span>
                <div className="communication-message-original">
                  <span>Английский оригинал</span>
                  <div>{message.content}</div>
                </div>
                {translationRevealed && message.translation_ru && (
                  <div className="communication-message-translation">
                    <span>Русский перевод</span>
                    <div>{message.translation_ru}</div>
                  </div>
                )}
              </div>
            ))}
          </div>

          <p className="note">
            {STATUS_LABELS[active.status] ?? active.status}
            {active.model ? ` · модель: ${active.model}` : ""}
          </p>

          {active.quote_assessment && (
            <div className="communication-assessment" role="status">
              <strong>
                {active.quote_assessment.is_complete
                  ? "Данные собраны — диалог завершён."
                  : "Проверка ответа поставщика"}
              </strong>
              <span>
                Цена: {active.quote_assessment.price ?? "не указана"}
                {active.quote_assessment.currency
                  ? ` ${active.quote_assessment.currency}`
                  : ""}
                {` · Incoterm: ${active.quote_assessment.incoterm ?? "не указан"}`}
                {` · MOQ: ${active.quote_assessment.moq ?? "не указан"}`}
              </span>
              {!active.quote_assessment.is_complete && (
                <span>
                  Не хватает: {active.quote_assessment.missing_fields
                    .map((field) => QUOTE_FIELD_LABELS[field] ?? field)
                    .join(", ")}
                </span>
              )}
            </div>
          )}

          {active.error && <p className="error">{active.error}</p>}

          {canContinue && (
            <div className="communication-reply">
              <Field label="Ваш ответ на RFQ от лица поставщика">
                <Textarea
                  rows={4}
                  placeholder="Введите ответ поставщика на английском"
                  value={supplierMessage}
                  onChange={(event) => setSupplierMessage(event.target.value)}
                />
              </Field>
              <div className="actions embedded-test-actions">
                <button
                  disabled={busy || !supplierMessage.trim()}
                  onClick={() => void reply(supplierMessage)}
                  type="button"
                >
                  {busy ? "Нейросеть отвечает…" : "Ответить"}
                </button>
                {(Object.entries(EXAMPLES) as Array<
                  [keyof typeof EXAMPLES, (typeof EXAMPLES)[keyof typeof EXAMPLES]]
                >).map(([kind, example]) => (
                  <button
                    className="secondary"
                    disabled={busy}
                    key={kind}
                    onClick={() => void reply(example.supplierMessage)}
                    type="button"
                  >
                    {example.title}
                  </button>
                ))}
              </div>
            </div>
          )}

          {!canContinue && (
            <button className="secondary" disabled={busy} onClick={() => void start()}>
              Начать заново
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function FullCommunicationTesting({
  embedded = false,
  rfq,
}: {
  embedded?: boolean;
  rfq?: RFQRead;
} = {}) {
  const [channel, setChannel] = useState<"email" | "whatsapp">("email");
  const [simulationMode, setSimulationMode] = useState<"buyer_ai" | "supplier_ai">(
    "buyer_ai",
  );
  const [recipient, setRecipient] = useState("");
  const [procurementContext, setProcurementContext] = useState(() =>
    rfq ? procurementContextFromRfq(rfq) : "",
  );
  const [supplierMessage, setSupplierMessage] = useState("");
  const [buyerMessage, setBuyerMessage] = useState("");
  const [instructions, setInstructions] = useState("");
  const [subject, setSubject] = useState(
    rfq?.rfq_subject ?? "Request for quotation",
  );
  const [deliveryMode, setDeliveryMode] = useState<"preview" | "send">(
    "preview",
  );
  const [history, setHistory] = useState<CommunicationTestRun[]>([]);
  const [active, setActive] = useState<CommunicationTestRun | null>(null);
  const [translationRevealed, setTranslationRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runExample = async (kind: keyof typeof EXAMPLES) => {
    const example = EXAMPLES[kind];
    if (!procurementContext.trim()) {
      setError("Сначала укажите общую информацию о закупке.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const dialog =
        active?.simulation_mode === "buyer_ai" && canContinue
          ? active
          : await api.runCommunicationTest({
              channel,
              recipient: "",
              procurement_context: procurementContext.trim(),
              additional_instructions: instructions.trim(),
              simulation_mode: "buyer_ai",
              initial_message: "",
              delivery_mode: "preview",
              subject: subject.trim() || "Request for quotation",
              confirm_external_send: false,
            });
      const updated = await api.continueCommunicationTest(dialog.id, {
        message: example.supplierMessage,
        recipient: "",
        confirm_external_send: false,
      });
      setSimulationMode("buyer_ai");
      setDeliveryMode("preview");
      setActive(updated);
      setTranslationRevealed(false);
      setSupplierMessage("");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

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
    setTranslationRevealed(false);
    setSupplierMessage("");
    setBuyerMessage("");
    setError(null);
  }, [rfq]);

  const startDialog = async () => {
    if (!procurementContext.trim()) return;
    const live = deliveryMode === "send" && simulationMode !== "supplier_ai";
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
        simulation_mode: simulationMode,
        initial_message: buyerMessage.trim(),
        delivery_mode: live ? "send" : "preview",
        subject: subject.trim() || "Request for quotation",
        confirm_external_send: live,
      });
      setActive(created);
      setTranslationRevealed(false);
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
    if (
      !active ||
      !(active.simulation_mode === "supplier_ai"
        ? buyerMessage.trim()
        : supplierMessage.trim())
    ) {
      return;
    }
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
        message:
          active.simulation_mode === "supplier_ai"
            ? buyerMessage.trim()
            : supplierMessage.trim(),
        recipient: recipient.trim(),
        confirm_external_send: live,
      });
      setActive(updated);
      setTranslationRevealed(false);
      setSupplierMessage("");
      setBuyerMessage("");
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
    setTranslationRevealed(false);
    setChannel(item.channel);
    setSimulationMode(item.simulation_mode);
    setProcurementContext(item.procurement_context);
    setInstructions(item.additional_instructions ?? "");
    setSubject(item.subject);
    setDeliveryMode(item.delivery_mode);
    setRecipient("");
    setSupplierMessage("");
    setBuyerMessage("");
    setError(null);
  };

  const canContinue =
    active !== null &&
    active.messages.length > 0 &&
    active.messages[active.messages.length - 1].sender_role ===
      (active.simulation_mode === "supplier_ai" ? "supplier" : "assistant") &&
    active.status !== "delivery_error" &&
    active.status !== "complete" &&
    active.status !== "escalated";

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
          {embedded ? <h3>Диалог с тестовым поставщиком</h3> : <h1>Тестирование общения</h1>}
          <p className="note">
            {embedded
              ? "Выберите готовый ответ или ручной режим. Сообщения никуда не отправляются."
              : "Администраторская песочница: выберите, будет ли нейросеть покупателем или поставщиком. В обоих случаях используется вся история диалога; это только симуляция без внешней отправки. Оригинал — на английском, для сотрудника показывается русский перевод."}
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
            <Field label="Кто пишет от нейросети">
              <Select
                value={simulationMode}
                onChange={(next) => {
                  const mode = next as "buyer_ai" | "supplier_ai";
                  setSimulationMode(mode);
                  if (mode === "supplier_ai") setDeliveryMode("preview");
                }}
                options={[
                  { value: "buyer_ai", label: "Покупатель" },
                  { value: "supplier_ai", label: "Поставщик" },
                ]}
              />
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
          {simulationMode === "supplier_ai" && (
            <Field
              label="Первое сообщение покупателя"
              hint="Нейросеть ответит в роли поставщика. Только симуляция, без отправки."
            >
              <Textarea
                rows={3}
                placeholder="Hello, we are looking for 50 kg of ammonia. Please send your quotation."
                value={buyerMessage}
                onChange={(event) => setBuyerMessage(event.target.value)}
              />
              {simulationMode === "supplier_ai" && deliveryMode === "send" && (
                <p className="note">Для нейросети-поставщика доступна только симуляция.</p>
              )}
            </Field>
          )}
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
          {embedded && (
            <div className="communication-test-examples">
              <strong>Примеры ответа поставщика</strong>
              <span>
                Запускают тестовый диалог одним нажатием. Внешняя отправка не
                выполняется.
              </span>
              <div className="actions">
                {(Object.entries(EXAMPLES) as Array<
                  [keyof typeof EXAMPLES, (typeof EXAMPLES)[keyof typeof EXAMPLES]]
                >).map(([kind, example]) => (
                  <button
                    className="secondary btn-small"
                    disabled={busy || !procurementContext.trim()}
                    key={kind}
                    onClick={() => void runExample(kind)}
                    type="button"
                  >
                    {example.title}
                  </button>
                ))}
              </div>
            </div>
          )}
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
                (simulationMode === "supplier_ai" && !buyerMessage.trim()) ||
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
          <div className="communication-dialog-heading">
            <h2>Диалог</h2>
            {active && (
              <DialogueTranslation
                run={active}
                revealed={translationRevealed}
                onRevealedChange={setTranslationRevealed}
                onTranslated={setActive}
              />
            )}
          </div>
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
                        : message.sender_role === "buyer"
                          ? "Вы · покупатель"
                        : active.channel === "email" &&
                            active.delivery_mode === "send"
                          ? "Поставщик · Email"
                          : "Вы · поставщик"}
                    </span>
                    <div className="communication-message-original">
                      <span>
                        {message.sender_role === "assistant"
                          ? "Английский оригинал"
                          : message.sender_role === "buyer"
                            ? "Оригинал покупателя"
                          : "Оригинал поставщика"}
                      </span>
                      <div>{message.content}</div>
                    </div>
                    {translationRevealed && message.translation_ru && (
                      <div className="communication-message-translation">
                        <span>Русский перевод</span>
                        <div>{message.translation_ru}</div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <p className="note">
                {STATUS_LABELS[active.status] ?? active.status}
                {active.model ? ` · модель: ${active.model}` : ""}
              </p>
              {active.quote_assessment && (
                <div className="communication-assessment" role="status">
                  <strong>
                    {active.quote_assessment.is_complete
                      ? "Данные собраны — нейросеть остановила диалог."
                      : "Проверка ответа поставщика"}
                  </strong>
                  <span>
                    Цена: {active.quote_assessment.price ?? "не указана"}
                    {active.quote_assessment.currency
                      ? ` ${active.quote_assessment.currency}`
                      : ""}
                    {` · Incoterm: ${active.quote_assessment.incoterm ?? "не указан"}`}
                    {` · MOQ: ${active.quote_assessment.moq ?? "не указан"}`}
                  </span>
                  <span>
                    Документы: {active.quote_assessment.has_coa ? "CoA" : ""}
                    {active.quote_assessment.has_coa && active.quote_assessment.has_tds
                      ? ", "
                      : ""}
                    {active.quote_assessment.has_tds ? "TDS" : ""}
                    {!active.quote_assessment.has_coa && !active.quote_assessment.has_tds
                      ? "не указаны"
                      : ""}
                  </span>
                  {!active.quote_assessment.is_complete && (
                    <span>
                      Не хватает: {active.quote_assessment.missing_fields
                        .map((field) => QUOTE_FIELD_LABELS[field] ?? field)
                        .join(", ")}
                    </span>
                  )}
                </div>
              )}
              {active.error && <p className="error">{active.error}</p>}
              {canContinue && (
                <div className="communication-reply">
                  <Field
                    label={
                      active.simulation_mode === "supplier_ai"
                        ? "Следующее сообщение покупателя"
                        : "Ответ поставщика (оригинал)"
                    }
                  >
                    <Textarea
                      rows={4}
                      placeholder={
                        active.simulation_mode === "supplier_ai"
                          ? "Напишите покупателю — нейросеть ответит как поставщик"
                          : "Введите английский ответ поставщика — нейросеть продолжит диалог"
                      }
                      value={
                        active.simulation_mode === "supplier_ai"
                          ? buyerMessage
                          : supplierMessage
                      }
                      onChange={(event) =>
                        active.simulation_mode === "supplier_ai"
                          ? setBuyerMessage(event.target.value)
                          : setSupplierMessage(event.target.value)
                      }
                    />
                  </Field>
                  <button
                    disabled={
                      busy ||
                      !(active.simulation_mode === "supplier_ai"
                        ? buyerMessage.trim()
                        : supplierMessage.trim()) ||
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

export default function CommunicationTesting({
  embedded = false,
  rfq,
}: {
  embedded?: boolean;
  rfq?: RFQRead;
} = {}) {
  if (embedded && rfq) {
    return <EmbeddedCommunicationTesting rfq={rfq} />;
  }
  return <FullCommunicationTesting embedded={embedded} rfq={rfq} />;
}
