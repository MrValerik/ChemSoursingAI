import { useEffect, useState } from "react";
import { getEchemiSender, updateEchemiSender, userErrorMessage } from "../api/client";
import type { EchemiSender, EchemiSenderFields } from "../api/types";
import { Field, Input } from "./ui";

export default function EchemiSenderSettings() {
  const [form, setForm] = useState<EchemiSenderFields | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  function populate(value: EchemiSender) {
    setForm({ email: value.email, company_name: value.company_name,
      contact_name: value.contact_name, phone: value.phone, country: value.country });
  }
  useEffect(() => {
    let alive = true;
    getEchemiSender().then(value => { if (alive) populate(value); })
      .catch(e => { if (alive) setError(userErrorMessage(e, "Не удалось загрузить отправителя Echemi.")); });
    return () => { alive = false; };
  }, []);
  function change(key: keyof EchemiSenderFields, value: string) {
    setForm(current => current ? { ...current, [key]: value } : current);
    setMessage("");
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!form || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      populate(await updateEchemiSender(form));
      setMessage("Данные отправителя Echemi сохранены.");
    } catch (e) {
      setError(userErrorMessage(e, "Не удалось сохранить отправителя Echemi."));
    } finally { setBusy(false); }
  }
  return <section className="panel" aria-labelledby="echemi-sender-heading">
    <h2 id="echemi-sender-heading">Echemi — данные отправителя</h2>
    <p className="muted">Общие контактные данные для обращений через форму Echemi. При отправке их увидит поставщик. Сохранение настроек не отправляет сообщения.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {!form && !error && <p role="status">Загружаем данные отправителя…</p>}
    {form && <form onSubmit={save}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <div className="row">
          <Field label="Email для ответа"><Input id="echemi-sender-email" type="email" autoComplete="email"
            maxLength={80} value={form.email} onChange={e => change("email", e.target.value)} /></Field>
          <Field label="Компания"><Input id="echemi-sender-company" autoComplete="organization"
            maxLength={120} value={form.company_name} onChange={e => change("company_name", e.target.value)} /></Field>
        </div>
        <div className="row">
          <Field label="Контактное лицо"><Input id="echemi-sender-contact" autoComplete="name"
            maxLength={100} value={form.contact_name} onChange={e => change("contact_name", e.target.value)} /></Field>
          <Field label="Телефон с кодом страны"><Input id="echemi-sender-phone" type="tel" autoComplete="tel"
            maxLength={40} placeholder="+7 …" value={form.phone} onChange={e => change("phone", e.target.value)} /></Field>
          <Field label="Код страны"><Input id="echemi-sender-country" list="echemi-sender-countries"
            maxLength={2} pattern="[A-Za-z]{2}" placeholder="RU" value={form.country}
            onChange={e => change("country", e.target.value.toUpperCase())} />
            <datalist id="echemi-sender-countries"><option value="RU">Россия</option><option value="CN">Китай</option>
              <option value="IN">Индия</option><option value="NL">Нидерланды</option></datalist>
          </Field>
        </div>
        <p className="muted">{Object.values(form).every(value => value.trim()) ? "Все контактные поля заполнены." : "Перед отправкой заполните все пять полей."}</p>
        <div className="actions"><button type="submit">{busy ? "Сохраняем…" : "Сохранить отправителя Echemi"}</button></div>
      </fieldset>
    </form>}
    {message && <p role="status">{message}</p>}
  </section>;
}
