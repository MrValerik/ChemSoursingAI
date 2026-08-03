import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CommunicationTestRun } from "../api/types";
import { Field, Input, Select, Textarea } from "./ui";

const STATUS_LABELS: Record<string, string> = {
  generating: "Нейросеть формирует ответ",
  previewed: "Симуляция завершена",
  sent: "Отправлено",
  llm_error: "Ошибка нейросети",
  delivery_error: "Ошибка доставки",
};

const STATUS_TONES: Record<string, string> = {
  generating: "tone-info",
  previewed: "tone-ok",
  sent: "tone-ok",
  llm_error: "tone-warn",
  delivery_error: "tone-warn",
};

export default function CommunicationTesting() {
  const [channel, setChannel] = useState<"email" | "whatsapp">("email");
  const [recipient, setRecipient] = useState("");
  const [customerMessage, setCustomerMessage] = useState("");
  const [language, setLanguage] = useState<"ru" | "en" | "zh">("ru");
  const [instructions, setInstructions] = useState("");
  const [subject, setSubject] = useState("Тест ChemSource AI");
  const [deliveryMode, setDeliveryMode] = useState<"preview" | "send">("preview");
  const [history, setHistory] = useState<CommunicationTestRun[]>([]);
  const [active, setActive] = useState<CommunicationTestRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = async () => {
    setHistory(await api.listCommunicationTests());
  };

  useEffect(() => {
    loadHistory().catch((reason) => setError(String(reason)));
  }, []);

  const run = async () => {
    if (!recipient.trim() || !customerMessage.trim()) return;
    const live = deliveryMode === "send";
    if (
      live &&
      !window.confirm(
        `Отправить сгенерированный ответ реально через ${
          channel === "email" ? "Email" : "WhatsApp"
        } на указанный адрес?`,
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
        customer_message: customerMessage.trim(),
        reply_language: language,
        additional_instructions: instructions.trim(),
        delivery_mode: deliveryMode,
        subject: subject.trim() || "Тест ChemSource AI",
        confirm_external_send: live,
      });
      setActive(created);
      await loadHistory();
    } catch (reason) {
      setError(String(reason));
      await loadHistory().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="requests-page communication-testing">
      <div className="requests-header">
        <div>
          <h1>Тестирование общения</h1>
          <p className="note">
            Администраторская песочница: сообщение контрагента передаётся
            локальной нейросети как недоверенный текст. По умолчанию результат
            остаётся только в приложении.
          </p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="communication-test-layout">
        <div className="panel">
          <h2>Сценарий</h2>
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
            <Field
              label={channel === "email" ? "Email получателя" : "Номер WhatsApp"}
              hint={
                channel === "whatsapp"
                  ? "Номер с кодом страны; свободный текст требует открытого 24-часового окна"
                  : undefined
              }
            >
              <Input
                placeholder={
                  channel === "email"
                    ? "test@example.com"
                    : "+7 900 000-00-00"
                }
                type={channel === "email" ? "email" : "tel"}
                value={recipient}
                onChange={(event) => setRecipient(event.target.value)}
              />
            </Field>
            <Field label="Язык ответа">
              <Select
                value={language}
                onChange={(next) => setLanguage(next as "ru" | "en" | "zh")}
                options={[
                  { value: "ru", label: "Русский" },
                  { value: "en", label: "Английский" },
                  { value: "zh", label: "Китайский" },
                ]}
              />
            </Field>
            <Field label="Режим">
              <Select
                value={deliveryMode}
                onChange={(next) => setDeliveryMode(next as "preview" | "send")}
                options={[
                  { value: "preview", label: "Только симуляция" },
                  { value: "send", label: "Сгенерировать и отправить реально" },
                ]}
              />
            </Field>
          </div>
          {channel === "email" && (
            <Field label="Тема тестового письма">
              <Input
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
              />
            </Field>
          )}
          <Field label="Сообщение контрагента">
            <Textarea
              rows={7}
              placeholder="Например: We can offer the material, but MOQ is 500 kg. Please confirm the required grade."
              value={customerMessage}
              onChange={(event) => setCustomerMessage(event.target.value)}
            />
          </Field>
          <Field
            label="Дополнительные инструкции"
            hint="Можно уточнить тон и цель ответа; ограничения безопасности изменить нельзя"
          >
            <Textarea
              rows={3}
              placeholder="Например: ответ должен быть кратким и запросить CoA"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
            />
          </Field>
          {deliveryMode === "send" && (
            <p className="external-action-warning">
              Это реальное внешнее действие. Перед отправкой появится отдельное
              подтверждение.
            </p>
          )}
          <div className="actions">
            <button
              disabled={busy || !recipient.trim() || !customerMessage.trim()}
              onClick={() => void run()}
            >
              {busy
                ? "Нейросеть отвечает…"
                : deliveryMode === "send"
                  ? "Сгенерировать и отправить"
                  : "Запустить симуляцию"}
            </button>
          </div>
        </div>

        <div className="panel">
          <h2>Ответ нейросети</h2>
          {active?.generated_reply ? (
            <>
              <div className="generated-message">{active.generated_reply}</div>
              <p className="note">
                Канал: {active.channel === "email" ? "Email" : "WhatsApp"} ·{" "}
                {STATUS_LABELS[active.status] ?? active.status}
                {active.model ? ` · модель: ${active.model}` : ""}
              </p>
            </>
          ) : (
            <p className="empty">
              Заполните сценарий и запустите симуляцию. Ответ появится здесь до
              любой внешней отправки.
            </p>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>Последние тесты</h2>
        {history.length === 0 ? (
          <p className="empty">Тесты ещё не запускались.</p>
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
                    {item.error && <div className="note">{item.error}</div>}
                  </td>
                  <td>
                    <button
                      className="secondary btn-small"
                      onClick={() => setActive(item)}
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
    </div>
  );
}
