import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  EmailIntegration,
  IntegrationConnectionResult,
  WhatsAppIntegration,
} from "../api/types";
import { Field, Input, Select } from "./ui";

type EmailForm = Omit<
  EmailIntegration,
  "channel" | "configured" | "source" | "smtp_password_set" | "imap_password_set"
>;

type WhatsAppForm = Omit<
  WhatsAppIntegration,
  "channel" | "configured" | "source" | "token_set"
>;

export default function IntegrationSettingsPanel() {
  const [email, setEmail] = useState<EmailIntegration | null>(null);
  const [whatsapp, setWhatsApp] = useState<WhatsAppIntegration | null>(null);
  const [emailForm, setEmailForm] = useState<EmailForm | null>(null);
  const [whatsappForm, setWhatsAppForm] = useState<WhatsAppForm | null>(null);
  const [smtpPassword, setSmtpPassword] = useState("");
  const [imapPassword, setImapPassword] = useState("");
  const [whatsappToken, setWhatsAppToken] = useState("");
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
      phone_id: whatsappData.phone_id,
      api_base_url: whatsappData.api_base_url,
      api_version: whatsappData.api_version,
      timeout_s: whatsappData.timeout_s,
    });
  };

  useEffect(() => {
    load().catch((reason) => setError(String(reason)));
  }, []);

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
        "Включить WhatsApp Cloud API? Сообщения будут передаваться через инфраструктуру Meta после явной команды отправки.",
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
              onChange={(event) =>
                setEmailForm({
                  ...emailForm,
                  delivery_mode: event.target.value as "demo" | "live",
                })
              }
            >
              <option value="demo">Demo — без внешней отправки</option>
              <option value="live">Live — реальная SMTP-отправка</option>
            </Select>
          </Field>
          <Field label="Автоматический дозапрос">
            <Select
              value={emailForm.auto_followup_mode}
              onChange={(event) =>
                setEmailForm({
                  ...emailForm,
                  auto_followup_mode: event.target.value as "off" | "draft" | "send",
                })
              }
            >
              <option value="off">Отключён</option>
              <option value="draft">Только черновик</option>
              <option value="send">Автоматическая отправка</option>
            </Select>
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
            <h2>WhatsApp Cloud API</h2>
            <p className="note">
              Канал передаёт сообщения через Meta. Свободный текст принимается
              провайдером только в открытом 24-часовом окне общения.
            </p>
          </div>
          <span className={`badge ${whatsapp.configured ? "tone-ok" : "tone-warn"}`}>
            {whatsapp.configured ? "настроен" : "не настроен"}
          </span>
        </div>
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
            Проверить Phone Number ID
          </button>
          <button
            className="secondary"
            disabled={busy !== null || !whatsapp.token_set}
            onClick={() => void clearWhatsAppToken()}
          >
            Удалить токен
          </button>
        </div>
      </div>
    </div>
  );
}
