import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getEchemiOutreachPreview, getEchemiDeliveries, sendEchemiOutreach, userErrorMessage } from "../api/client";
import type { EchemiSearch, EchemiSender, EchemiDelivery } from "../api/types";

const labels: Record<string, string> = {queued: "В очереди", sending: "Отправляем",
  sent: "Отправлено", blocked: "Не отправлено", unknown: "Нужна проверка результата"};

export default function EchemiOutreach({rfqId, search, canSend}: {rfqId: number; search: EchemiSearch; canSend: boolean}) {
  const [sender, setSender] = useState<EchemiSender | null>(null);
  const [message, setMessage] = useState("");
  const [senderVersion, setSenderVersion] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [rows, setRows] = useState<EchemiDelivery[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try { const data = await getEchemiDeliveries(rfqId); if (alive) setRows(data); }
      catch (e) { if (alive) setError(userErrorMessage(e, "Не удалось загрузить историю отправки.")); }
      finally { if (alive) timer = setTimeout(refresh, 5000); }
    }
    void refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [rfqId]);
  async function prepare() {
    setBusy(true); setError(""); setConfirmed(false);
    try {
      const preview = await getEchemiOutreachPreview(rfqId);
      setSender(preview.sender); setSenderVersion(preview.sender_version); setMessage(preview.message); setOpened(true);
    } catch (e) { setError(userErrorMessage(e, "Не удалось подготовить обращение.")); }
    finally { setBusy(false); }
  }
  async function send() {
    setBusy(true); setError("");
    try {
      await sendEchemiOutreach(rfqId, {search_id: search.id, product_urls: chosen, message, confirmed, sender_version: senderVersion});
      setRows(await getEchemiDeliveries(rfqId)); setChosen([]); setConfirmed(false); setOpened(false);
    } catch (e) { setError(userErrorMessage(e, "Не удалось поставить рассылку в очередь.")); }
    finally { setBusy(false); }
  }
  const candidates = search.results.filter(r => r.product_url && r.seller_name);
  return <section aria-label="Рассылка через формы Echemi">
    <h3>Обращения через формы Echemi</h3>
    <p className="muted">Выберите компании и проверьте текст. После запуска формы заполняются и отправляются автоматически.
      При CAPTCHA или неизвестных полях отправка остановится. Неопределённый результат не повторяется автоматически.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {canSend && !opened && <button onClick={prepare} disabled={busy || !candidates.length || !["completed", "partial"].includes(search.status)}>
      Подготовить рассылку Echemi</button>}
    {canSend && opened && <fieldset disabled={busy}>
      <legend>Получатели и текст обращения</legend>
      {!sender?.configured && <p role="alert">Заполните данные отправителя в <Link to="/settings">настройках</Link>, затем заново подготовьте рассылку.</p>}
      {sender && <details><summary>Контакты, которые будут использованы: {sender.contact_name} · {sender.email}</summary>
        <p>{[sender.company_name, sender.phone, sender.country, sender.city, sender.region,
          sender.address, sender.postal_code, sender.job_title, sender.website, sender.whatsapp, sender.wechat].filter(Boolean).join(" · ")}</p>
      </details>}
      {candidates.map(r => {
        const previous = rows.find(d => d.product_url === r.product_url || d.seller_name === r.seller_name);
        const disabled = !!previous && previous.status !== "blocked";
        return <label key={r.product_url} style={{display: "block", margin: "8px 0"}}>
          <input type="checkbox" checked={chosen.includes(r.product_url!)} disabled={disabled}
            onChange={e => {setConfirmed(false); setChosen(old => e.target.checked ? [...old, r.product_url!] : old.filter(url => url !== r.product_url));}} />
          {" "}{r.seller_name} — {r.title}
          {" "}<a href={r.product_url!} target="_blank" rel="noreferrer">Карточка</a>
          {previous && " · " + labels[previous.status]}
        </label>;
      })}
      <label style={{display: "block"}}>Текст обращения (английский)
        <textarea rows={12} maxLength={5000} value={message} style={{width: "100%"}}
          onChange={e => {setMessage(e.target.value); setConfirmed(false);}} />
      </label>
      <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
        {" "}Проверены компании, соответствие товара запросу, текст и контакты. Разрешаю отправить обращения через Echemi.</label>
      <div className="actions">
        <button onClick={send} disabled={!sender?.configured || !confirmed || !chosen.length || message.trim().length < 20 || message.length > 5000}>
          {busy ? "Ставим в очередь…" : "Отправить через формы Echemi (" + chosen.length + ")"}</button>
        <button className="secondary" onClick={() => setOpened(false)}>Отмена</button>
      </div>
    </fieldset>}
    {rows.length > 0 && <ul>{rows.map(r => <li key={r.id}><a href={r.product_url} target="_blank" rel="noreferrer">{r.seller_name}</a>
      {" · "}<strong>{labels[r.status] || r.status}</strong> — {r.message || "Ожидает свободного браузера."}</li>)}</ul>}
  </section>;
}
