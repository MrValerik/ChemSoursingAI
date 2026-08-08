import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  EmailIntegration,
  IntegrationConnectionResult,
  WhatsAppIntegration,
  WhatsAppWebStatus,
} from "../api/types";
import { Field, Input, Select } from "./ui";

type EmailForm = Omit<
  EmailIntegration,
  "channel" | "configured" | "source" | "smtp_password_set" | "imap_password_set"
>;

type WhatsAppForm = Omit<
  WhatsAppIntegration,
  | "channel"
  | "configured"
  | "source"
  | "token_set"
  | "web_gateway_available"
>;

function whatsappWebErrorMessage(status: WhatsAppWebStatus): string | null {
  if (!status.error) return null;
  const messages: Record<string, string> = {
    proxy_connection_failed:
      "Не удалось подключиться к настроенному прокси. Проверьте Xray и VLESS-конфигурацию.",
    whatsapp_web_proxy_timeout:
      "Прокси не смог открыть web.whatsapp.com. Проверьте доступность VLESS-сервера.",
    whatsapp_web_connection_timeout:
      "Сервер не может открыть web.whatsapp.com напрямую. Настройте прокси или VPN.",
    whatsapp_web_dns_failed:
      "Не удалось определить адрес web.whatsapp.com. Проверьте DNS и прокси.",
    whatsapp_web_initialization_failed:
      "WhatsApp Web не загрузился. Шлюз автоматически повторяет подключение.",
  };
  return messages[status.error] ?? `Ошибка WhatsApp Web: ${status.error}`;
}

export default function IntegrationSettingsPanel() {
  const [email, setEmail] = useState<EmailIntegration | null>(null);
  const [whatsapp, setWhatsApp] = useState<WhatsAppIntegration | null>(null);
  const [emailForm, setEmailForm] = useState<EmailForm | null>(null);
  const [whatsappForm, setWhatsAppForm] = useState<WhatsAppForm | null>(null);
  const [smtpPassword, setSmtpPassword] = useState("");
  const [imapPassword, setImapPassword] = useState("");
  const [whatsappToken, setWhatsAppToken] = useState("");
  const [webStatus, setWebStatus] = useState<WhatsAppWebStatus | null>(null);
  const [webQr, setWebQr] = useState<string | null>(null);
  const [webPhoneNumber, setWebPhoneNumber] = useState("");
  const [webPairingCode, setWebPairingCode] = useState<string | null>(null);
  const [webPairingExpires, setWebPairingExpires] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IntegrationConnectionResult | null>(null);

  const load = async () => {
    const [emailData, whatsappData] = await Promise.all([
      api.getEmailIntegration(),
      api.getWhatsAppIntegration(),
    ]);
    setEmail(emailData);
    setWhatsApp(whatsappData);
    setEmailForm({
      enabled: emailData.enabled,
      delivery_mode: emailData.delivery_mode,
      email_from: emailData.email_from,
      email_from_name: emailData.email_from_name,
      email_timeout_s: emailData.email_timeout_s,
      auto_followup_mode: emailData.auto_followup_mode,
      smtp_host: emailData.smtp_host,
      smtp_port: emailData.smtp_port,
      smtp_user: emailData.smtp_user,
      smtp_use_ssl: emailData.smtp_use_ssl,
      smtp_starttls: emailData.smtp_starttls,
      imap_host: emailData.imap_host,
      imap_port: emailData.imap_port,
      imap_user: emailData.imap_user,
      imap_use_ssl: emailData.imap_use_ssl,
      imap_folder: emailData.imap_folder,
    });
    setWhatsAppForm({
      enabled: whatsappData.enabled,
      transport: whatsappData.transport,
      phone_id: whatsappData.phone_id,
      api_base_url: whatsappData.api_base_url,
      api_version: whatsappData.api_version,
      timeout_s: whatsappData.timeout_s,
    });
    if (whatsappData.transport === "web" && whatsappData.web_gateway_available) {
      api.getWhatsAppWebStatus().then(setWebStatus).catch(() => setWebStatus(null));
    }
  };

  useEffect(() => {
    load().catch((reason) => setError(String(reason)));
  }, []);

  useEffect(() => {
    if (
      whatsappForm?.transport !== "web" ||
      !whatsapp?.web_gateway_available ||
      webStatus?.ready
    ) {
      return undefined;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await api.getWhatsAppWebStatus();
        if (cancelled) return;
        setWebStatus(status);
        setWebPairingExpires(status.pairing_code_expires_in_seconds);
        if (!status.pairing_code_available) {
          setWebPairingCode(null);
        }
        if (status.ready || status.state === "authenticated") {
          setWebPairingCode(null);
          setWebQr(null);
        } else if (status.qr_available && !status.pairing_code_available) {
          const qr = await api.getWhatsAppWebQr();
          if (!cancelled) setWebQr(qr.qr_data_url);
        } else if (!status.qr_available) {
          setWebQr(null);
        }
      } catch (_reason) {
        // Ручные действия показывают ошибки; фоновый опрос не засоряет интерфейс.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    whatsappForm?.transport,
    whatsapp?.web_gateway_available,
    webStatus?.ready,
  ]);

  const saveEmail = async () => {
    if (!emailForm) return;
    if (
      emailForm.enabled &&
      emailForm.delivery_mode === "live" &&
      !window.confirm(
        "Включить реальную SMTP-отправку? После сохранения действия отправки в интерфейсе смогут доставлять письма наружу.",
      )
    ) {
      return;
    }
    setBusy("email-save");
    setResult(null);
    try {
      const saved = await api.updateEmailIntegration({
        ...emailForm,
        smtp_password: smtpPassword || null,
        imap_password: imapPassword || null,
      });
      setEmail(saved);
      setSmtpPassword("");
      setImapPassword("");
      await load();
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const saveWhatsApp = async () => {
    if (!whatsappForm) return;
    if (
      whatsappForm.enabled &&
      !window.confirm(
        whatsappForm.transport === "web"
          ? "Включить неофициальное подключение WhatsApp Web? Оно может нарушать правила WhatsApp и привести к блокировке номера."
          : "Включить WhatsApp Cloud API? Сообщения будут передаваться через инфраструктуру Meta после явной команды отправки.",
      )
    ) {
      return;
    }
    setBusy("whatsapp-save");
    setResult(null);
    try {
      const saved = await api.updateWhatsAppIntegration({
        ...whatsappForm,
        access_token: whatsappToken || null,
      });
      setWhatsApp(saved);
      setWhatsAppToken("");
      await load();
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const clearEmailSecrets = async () => {
    if (
      !emailForm ||
      !window.confirm("Удалить сохранённые SMTP/IMAP-пароли и отключить Email?")
    ) {
      return;
    }
    setBusy("email-clear");
    try {
      await api.updateEmailIntegration({
        ...emailForm,
        enabled: false,
        smtp_password: null,
        imap_password: null,
        clear_secrets: true,
      });
      setSmtpPassword("");
      setImapPassword("");
      await load();
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const clearWhatsAppToken = async () => {
    if (
      !whatsappForm ||
      !window.confirm("Удалить сохранённый Meta Access Token и отключить WhatsApp?")
    ) {
      return;
    }
    setBusy("whatsapp-clear");
    try {
      await api.updateWhatsAppIntegration({
        ...whatsappForm,
        enabled: false,
        access_token: null,
        clear_token: true,
      });
      setWhatsAppToken("");
      await load();
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const check = async (channel: "email" | "whatsapp") => {
    setBusy(`${channel}-check`);
    setResult(null);
    try {
      const checked =
        channel === "email"
          ? await api.checkEmailIntegration()
          : await api.checkWhatsAppIntegration();
      setResult(checked);
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const refreshWhatsAppWeb = async (withQr = true) => {
    setBusy("whatsapp-web-status");
    try {
      const status = await api.getWhatsAppWebStatus();
      setWebStatus(status);
      if (withQr && status.qr_available) {
        setWebQr((await api.getWhatsAppWebQr()).qr_data_url);
      } else if (!status.qr_available) {
        setWebQr(null);
      }
      if (status.ready || status.state === "authenticated") {
        setWebPairingCode(null);
      }
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const connectWhatsAppWeb = async () => {
    setBusy("whatsapp-web-connect");
    setWebQr(null);
    setWebPairingCode(null);
    try {
      if (webStatus?.pairing_code_available) {
        await api.cancelWhatsAppWebPairingCode();
      }
      let status = await api.connectWhatsAppWeb();
      setWebStatus(status);
      for (
        let attempt = 0;
        attempt < 30 &&
        !status.ready &&
        !status.qr_available &&
        status.state !== "error";
        attempt += 1
      ) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        status = await api.getWhatsAppWebStatus();
        setWebStatus(status);
      }
      if (status.qr_available) {
        setWebQr((await api.getWhatsAppWebQr()).qr_data_url);
      }
      setError(whatsappWebErrorMessage(status));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const disconnectWhatsAppWeb = async () => {
    if (!window.confirm("Отключить связанную сессию WhatsApp Web? Потребуется новый QR-код.")) return;
    setBusy("whatsapp-web-disconnect");
    try {
      setWebStatus(await api.disconnectWhatsAppWeb());
      setWebQr(null);
      setWebPairingCode(null);
      setWebPairingExpires(0);
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const createWhatsAppWebPairingCode = async () => {
    if (!webPhoneNumber.trim()) {
      setError("Укажите номер WhatsApp с кодом страны");
      return;
    }
    setBusy("whatsapp-web-pairing-code");
    setWebQr(null);
    setWebPairingCode(null);
    try {
      let status = await api.connectWhatsAppWeb();
      for (
        let attempt = 0;
        attempt < 30 &&
        !status.ready &&
        !status.qr_available &&
        status.state !== "pairing_code" &&
        status.state !== "error";
        attempt += 1
      ) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        status = await api.getWhatsAppWebStatus();
      }
      if (status.ready) {
        setWebStatus(status);
        setError("WhatsApp уже подключён");
        return;
      }
      const connectionError = whatsappWebErrorMessage(status);
      if (connectionError) {
        setWebStatus(status);
        setError(connectionError);
        return;
      }
      const result = await api.createWhatsAppWebPairingCode(webPhoneNumber);
      setWebPairingCode(result.pairing_code);
      setWebPairingExpires(result.expires_in_seconds);
      setWebPhoneNumber("");
      setWebStatus(await api.getWhatsAppWebStatus());
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  const cancelWhatsAppWebPairingCode = async () => {
    setBusy("whatsapp-web-pairing-cancel");
    try {
      setWebStatus(await api.cancelWhatsAppWebPairingCode());
      setWebPairingCode(null);
      setWebPairingExpires(0);
      setError(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  };

  if (!email || !whatsapp || !emailForm || !whatsappForm) {
    return (
      <div className="panel">
        <h2>Подключения</h2>
        <p className="note">{error ?? "Загрузка настроек каналов…"}</p>
      </div>
    );
  }

  return (
    <div className="integration-settings">
      {error && <p className="error">{error}</p>}
      {result && (
        <p className="success">
          {result.message}
        </p>
      )}

      <div className="panel">
        <div className="tab-toolbar">
          <div>
            <h2>Email — SMTP и IMAP</h2>
            <p className="note">
              Пароли сохраняются в БД только в зашифрованном виде и не
              возвращаются браузеру.
            </p>
          </div>
          <span className={`badge ${email.configured ? "tone-ok" : "tone-warn"}`}>
            {email.configured ? "настроен" : "не настроен"}
          </span>
        </div>

        <div className="settings-grid">
          <Field label="Адрес отправителя">
            <Input
              type="email"
              value={emailForm.email_from}
              onChange={(event) =>
                setEmailForm({ ...emailForm, email_from: event.target.value })
              }
            />
          </Field>
          <Field label="Имя отправителя">
            <Input
              value={emailForm.email_from_name}
              onChange={(event) =>
                setEmailForm({ ...emailForm, email_from_name: event.target.value })
              }
            />
          </Field>
          <Field label="SMTP-сервер">
            <Input
              value={emailForm.smtp_host}
              onChange={(event) =>
                setEmailForm({ ...emailForm, smtp_host: event.target.value })
              }
            />
          </Field>
          <Field label="SMTP-порт">
            <Input
              min={1}
              max={65535}
              type="number"
              value={emailForm.smtp_port}
              onChange={(event) =>
                setEmailForm({
                  ...emailForm,
                  smtp_port: Number(event.target.value),
                })
              }
            />
          </Field>
          <Field label="SMTP-логин">
            <Input
              value={emailForm.smtp_user}
              onChange={(event) =>
                setEmailForm({ ...emailForm, smtp_user: event.target.value })
              }
            />
          </Field>
          <Field
            label="SMTP-пароль"
            hint={email.smtp_password_set ? "Пароль уже сохранён; пустое поле его не изменит" : undefined}
          >
            <Input
              autoComplete="new-password"
              type="password"
              value={smtpPassword}
              onChange={(event) => setSmtpPassword(event.target.value)}
            />
          </Field>
          <Field label="IMAP-сервер">
            <Input
              value={emailForm.imap_host}
              onChange={(event) =>
                setEmailForm({ ...emailForm, imap_host: event.target.value })
              }
            />
          </Field>
          <Field label="IMAP-порт">
            <Input
              min={1}
              max={65535}
              type="number"
              value={emailForm.imap_port}
              onChange={(event) =>
                setEmailForm({
                  ...emailForm,
                  imap_port: Number(event.target.value),
                })
              }
            />
          </Field>
          <Field label="IMAP-логин">
            <Input
              value={emailForm.imap_user}
              onChange={(event) =>
                setEmailForm({ ...emailForm, imap_user: event.target.value })
              }
            />
          </Field>
          <Field
            label="IMAP-пароль"
            hint={email.imap_password_set ? "Пароль уже сохранён; пустое поле его не изменит" : undefined}
          >
            <Input
              autoComplete="new-password"
              type="password"
              value={imapPassword}
              onChange={(event) => setImapPassword(event.target.value)}
            />
          </Field>
          <Field label="Папка IMAP">
            <Input
              value={emailForm.imap_folder}
              onChange={(event) =>
                setEmailForm({ ...emailForm, imap_folder: event.target.value })
              }
            />
          </Field>
          <Field label="Режим отправки">
            <Select
              value={emailForm.delivery_mode}
              onChange={(next) =>
                setEmailForm({
                  ...emailForm,
                  delivery_mode: next as "demo" | "live",
                })
              }
              options={[
                { value: "demo", label: "Demo — без внешней отправки" },
                { value: "live", label: "Live — реальная SMTP-отправка" },
              ]}
            />
          </Field>
          <Field label="Автоматический дозапрос">
            <Select
              value={emailForm.auto_followup_mode}
              onChange={(next) =>
                setEmailForm({
                  ...emailForm,
                  auto_followup_mode: next as "off" | "draft" | "send",
                })
              }
              options={[
                { value: "off", label: "Отключён" },
                { value: "draft", label: "Только черновик" },
                { value: "send", label: "Автоматическая отправка" },
              ]}
            />
          </Field>
        </div>

        <div className="settings-checks">
          <label>
            <input
              checked={emailForm.smtp_use_ssl}
              type="checkbox"
              onChange={(event) =>
                setEmailForm({ ...emailForm, smtp_use_ssl: event.target.checked })
              }
            />
            SMTP SSL
          </label>
          <label>
            <input
              checked={emailForm.smtp_starttls}
              type="checkbox"
              onChange={(event) =>
                setEmailForm({ ...emailForm, smtp_starttls: event.target.checked })
              }
            />
            SMTP STARTTLS
          </label>
          <label>
            <input
              checked={emailForm.imap_use_ssl}
              type="checkbox"
              onChange={(event) =>
                setEmailForm({ ...emailForm, imap_use_ssl: event.target.checked })
              }
            />
            IMAP SSL
          </label>
          <label>
            <input
              checked={emailForm.enabled}
              type="checkbox"
              onChange={(event) =>
                setEmailForm({ ...emailForm, enabled: event.target.checked })
              }
            />
            Канал включён
          </label>
        </div>
        <div className="actions">
          <button disabled={busy !== null} onClick={() => void saveEmail()}>
            Сохранить Email
          </button>
          <button
            className="secondary"
            disabled={busy !== null || !email.configured}
            onClick={() => void check("email")}
          >
            Проверить SMTP и IMAP
          </button>
          <button
            className="secondary"
            disabled={busy !== null || (!email.smtp_password_set && !email.imap_password_set)}
            onClick={() => void clearEmailSecrets()}
          >
            Удалить пароли
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="tab-toolbar">
          <div>
            <h2>WhatsApp</h2>
            <p className="note">
              Cloud API — официальный вариант Meta. WhatsApp Web подключает
              обычный номер через связанное устройство и может привести к
              блокировке аккаунта.
            </p>
          </div>
          <span className={`badge ${whatsapp.configured ? "tone-ok" : "tone-warn"}`}>
            {whatsapp.configured ? "настроен" : "не настроен"}
          </span>
        </div>
        <Field label="Способ подключения">
          <Select
            value={whatsappForm.transport}
            onChange={(value) => {
              setWhatsAppForm({
                ...whatsappForm,
                transport: value as "cloud_api" | "web",
                enabled: false,
              });
              setWebQr(null);
            }}
            options={[
              { value: "cloud_api", label: "Cloud API (официальный)" },
              { value: "web", label: "WhatsApp Web (для тестирования)" },
            ]}
          />
        </Field>
        {whatsappForm.transport === "cloud_api" ? (
        <div className="settings-grid">
          <Field label="Phone Number ID">
            <Input
              value={whatsappForm.phone_id}
              onChange={(event) =>
                setWhatsAppForm({
                  ...whatsappForm,
                  phone_id: event.target.value,
                })
              }
            />
          </Field>
          <Field
            label="Access Token"
            hint={whatsapp.token_set ? "Токен уже сохранён; пустое поле его не изменит" : undefined}
          >
            <Input
              autoComplete="new-password"
              type="password"
              value={whatsappToken}
              onChange={(event) => setWhatsAppToken(event.target.value)}
            />
          </Field>
          <Field label="Graph API URL">
            <Input
              value={whatsappForm.api_base_url}
              onChange={(event) =>
                setWhatsAppForm({
                  ...whatsappForm,
                  api_base_url: event.target.value,
                })
              }
            />
          </Field>
          <Field label="Версия Graph API">
            <Input
              value={whatsappForm.api_version}
              onChange={(event) =>
                setWhatsAppForm({
                  ...whatsappForm,
                  api_version: event.target.value,
                })
              }
            />
          </Field>
        </div>
        ) : (
          <div className="settings-stack">
            <p className="note warning-text">
              Используйте отдельный номер. QR-код доступен только администраторам;
              сессия хранится на сервере в закрытом Docker-томе. Это неофициальная
              автоматизация WhatsApp Web без гарантий стабильности.
            </p>
            {!whatsapp.web_gateway_available && (
              <p className="error">
                На сервере не задан WHATSAPP_WEB_SERVICE_TOKEN.
              </p>
            )}
            {webStatus && (
              <p className={webStatus.ready ? "success" : "note"}>
                Состояние: {webStatus.state}
                {webStatus.account ? ` · номер ${webStatus.account}` : ""}
                {webStatus.client_state ? ` · WhatsApp ${webStatus.client_state}` : ""}
                {webStatus.loading_percent !== null
                  ? ` · загрузка ${webStatus.loading_percent}%`
                  : ""}
                {webStatus.pending_events ? ` · очередь ${webStatus.pending_events}` : ""}
              </p>
            )}
            {webStatus && whatsappWebErrorMessage(webStatus) && (
              <p className="error" role="alert">
                {whatsappWebErrorMessage(webStatus)}
                {webStatus.proxy_configured ? " Прокси включён." : " Прокси не настроен."}
              </p>
            )}
            {!webStatus?.ready && (
              <div className="settings-stack whatsapp-pairing-panel">
                <h3>Подключение по номеру — рекомендуется</h3>
                <p className="note">
                  Введите номер подключаемого WhatsApp с кодом страны. Номер
                  используется только для запроса временного кода и не сохраняется.
                </p>
                <div className="settings-grid">
                  <Field label="Номер WhatsApp" hint="Например: +7 900 000-00-00">
                    <Input
                      autoComplete="tel"
                      inputMode="tel"
                      placeholder="+7 900 000-00-00"
                      value={webPhoneNumber}
                      onChange={(event) => setWebPhoneNumber(event.target.value)}
                    />
                  </Field>
                </div>
                <div className="actions">
                  <button
                    disabled={busy !== null || !whatsapp.web_gateway_available}
                    onClick={() => void createWhatsAppWebPairingCode()}
                  >
                    Получить код привязки
                  </button>
                  {webPairingCode && (
                    <button
                      className="secondary"
                      disabled={busy !== null}
                      onClick={() => void cancelWhatsAppWebPairingCode()}
                    >
                      Отменить код
                    </button>
                  )}
                </div>
                {webPairingCode && (
                  <div className="whatsapp-pairing-code" aria-live="polite">
                    <span className="note">Временный код</span>
                    <strong>{webPairingCode.replace(/(.{4})(?=.)/g, "$1 ")}</strong>
                    <span className="note">
                      Действует ещё примерно {webPairingExpires} секунд. На телефоне:
                      WhatsApp → Связанные устройства → Привязка устройства →
                      Привязать по номеру телефона.
                    </span>
                  </div>
                )}
              </div>
            )}
            {webQr && (
              <div>
                <p className="note">
                  Запасной способ: QR обновляется автоматически каждые несколько
                  секунд. WhatsApp → Связанные устройства → Привязка устройства.
                </p>
                <img
                  src={webQr}
                  alt="QR-код подключения WhatsApp Web"
                  width={280}
                  height={280}
                />
              </div>
            )}
            <div className="actions">
              <button
                className="secondary"
                disabled={busy !== null || !whatsapp.web_gateway_available}
                onClick={() => void connectWhatsAppWeb()}
              >
                Показать QR-код
              </button>
              <button
                className="secondary"
                disabled={busy !== null || !whatsapp.web_gateway_available}
                onClick={() => void refreshWhatsAppWeb(true)}
              >
                Обновить состояние и QR
              </button>
              <button
                className="secondary"
                disabled={busy !== null || !webStatus?.ready}
                onClick={() => void disconnectWhatsAppWeb()}
              >
                Отключить сессию
              </button>
            </div>
          </div>
        )}
        <div className="settings-checks">
          <label>
            <input
              checked={whatsappForm.enabled}
              type="checkbox"
              onChange={(event) =>
                setWhatsAppForm({
                  ...whatsappForm,
                  enabled: event.target.checked,
                })
              }
            />
            Канал включён
          </label>
        </div>
        <div className="actions">
          <button disabled={busy !== null} onClick={() => void saveWhatsApp()}>
            Сохранить WhatsApp
          </button>
          <button
            className="secondary"
            disabled={busy !== null || !whatsapp.configured}
            onClick={() => void check("whatsapp")}
          >
            Проверить подключение
          </button>
          {whatsappForm.transport === "cloud_api" && (
          <button
            className="secondary"
            disabled={busy !== null || !whatsapp.token_set}
            onClick={() => void clearWhatsAppToken()}
          >
            Удалить токен
          </button>
          )}
        </div>
      </div>
    </div>
  );
}
