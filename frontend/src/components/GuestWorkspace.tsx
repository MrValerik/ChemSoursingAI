import { useState } from "react";
import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { demoRequests, type DemoRequest } from "../demo/requests";
import { applyTheme, readTheme } from "../theme";
import { LogoMark, LogoWord } from "./Logo";
import "./guest.css";

const tabs = [
  ["overview", "Запрос"], ["suppliers", "Поставщики"],
  ["messages", "Общение"], ["comparison", "Сравнение предложений"],
] as const;
const money = (value: number) => value.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function RequestList() {
  const [query, setQuery] = useState("");
  const requests = demoRequests.filter((r) => `${r.name} ${r.english} ${r.cas} ${r.id}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    <h1>Демонстрационные запросы</h1>
    <p className="note">Откройте запрос, чтобы посмотреть путь от требований к сырью до проверки поставщиков и сравнения ответов.</p>
    <div className="field guest-search"><label htmlFor="demo-search">Поиск по веществу, CAS или номеру</label>
      <input id="demo-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Например, глицерин" />
    </div>
    <div className="guest-cards">{requests.map((r) => <Link className="guest-card" key={r.id} to={`/demo/requests/${r.id}/overview`}>
      <div className="guest-card-top"><span>{r.id}</span><span className="guest-status">{r.status}</span></div>
      <h2>{r.name}</h2><p>CAS {r.cas} · {r.quantity.toLocaleString("ru-RU")} кг · {r.grade} грейд</p>
      <p className="note">{r.purpose}</p><strong>Открыть запрос →</strong>
    </Link>)}</div>
    {requests.length === 0 && <div className="panel"><p>Запросы не найдены. Попробуйте другое название или CAS.</p><button onClick={() => setQuery("")}>Сбросить поиск</button></div>}
  </>;
}

function Overview({ request: r }: { request: DemoRequest }) {
  return <div className="panel"><h2>Требования к закупке</h2>
    <dl className="guest-facts"><div><dt>Вещество</dt><dd>{r.english}</dd></div><div><dt>CAS</dt><dd>{r.cas}</dd></div>
      <div><dt>Количество</dt><dd>{r.quantity.toLocaleString("ru-RU")} кг</dd></div><div><dt>Грейд</dt><dd>{r.grade}</dd></div>
      <div><dt>Страны поиска</dt><dd>Китай, Индия</dd></div><div><dt>Запрашиваемый базис</dt><dd>FCA, площадка поставщика (Incoterms 2020)</dd></div></dl>
    <h2>Спецификация и документы</h2><p>{r.specification}</p>
    <h2>Что показывает этот пример</h2><p>{r.purpose}</p>
    <p className="note">Следующий шаг: откройте вкладку «Поставщики», изучите доказательства и причины риска.</p>
  </div>;
}

function Suppliers({ request: r }: { request: DemoRequest }) {
  return <div className="guest-cards">{r.suppliers.map((s) => <article className="panel" key={s.name}>
    <h2>{s.name}</h2><p>{s.country} · {s.role}</p>
    <details><summary>Посмотреть доказательство</summary>
      <p className="note">Источник: учебная карточка, подготовленная для демо; язык оригинала — английский. Это не проверенный сайт реальной компании.</p>
      <blockquote lang="en">{s.evidence}</blockquote>
    </details>
    <p className="guest-risk"><strong>Что проверить: </strong>{s.risk}</p>
  </article>)}</div>;
}

function Messages({ request: r }: { request: DemoRequest }) {
  const s = r.suppliers[0];
  return <>
    <p className="note">Пример переписки с {s.name}. Все сообщения учебные; письма не отправлялись.</p>
    <article className="panel"><h2>Закупщик → поставщику · Запрос предложения</h2>
      <p lang="en">Dear Sales Team, please quote {r.quantity} kg of {r.english}, CAS {r.cas}. Please confirm the grade, purity, MOQ, price in USD/kg, FCA named place (Incoterms 2020), lead time, payment terms and sample availability. Please provide CoA and TDS.</p>
    </article>
    {s.price !== null ? <>
      <article className="panel"><h2>Поставщик → закупщику · Ответ</h2><p lang="en">Thank you for your inquiry. We offer {r.quantity} kg of {r.english} at USD {s.price.toFixed(2)}/kg, FCA our factory, Incoterms 2020. MOQ: {s.moq.replace("кг", "kg")}. Payment: 30% advance, 70% before shipment. {s.lead === "Не указан" ? "We will confirm the production schedule shortly." : "Lead time: 30 days."} CoA and TDS can be provided for review.</p></article>
      <article className="panel"><h2>Закупщик · Подготовленный дозапрос</h2><p lang="en">Please confirm the full FCA pickup address, sample availability and provide the batch CoA and TDS for review.{s.lead === "Не указан" && " Please also confirm the lead time for our requested quantity."}</p><p className="note">Следующий шаг специалиста: проверить документы и недостающие условия.</p></article>
    </> : <div className="panel"><h2>Ожидаем предложение</h2><p>В этом сценарии проверка поставщиков ещё не завершена. Ответ и цена не подставляются без данных.</p></div>}
  </>;
}

function Comparison({ request: r }: { request: DemoRequest }) {
  return <>
    <p className="note">Учебные цены в USD за кг, FCA площадка поставщика, Incoterms 2020. Перевозка, пошлины и налоги не включены. Площадки разные: итоговую стоимость доставки нужно рассчитать отдельно.</p>
    <div className="guest-table"><table><thead><tr><th>Поставщик</th><th>Цена, USD/кг</th><th>За {r.quantity.toLocaleString("ru-RU")} кг, USD</th><th>MOQ</th><th>Срок</th><th>Оплата</th><th>Риски / следующий шаг</th></tr></thead>
      <tbody>{r.suppliers.map((s) => <tr key={s.name}><td>{s.name}<p className="note">{s.role}</p></td><td>{s.price === null ? "Не получена" : money(s.price)}</td><td>{s.price === null ? "Нет данных" : money(s.price * r.quantity)}</td><td>{s.moq}</td><td>{s.lead}</td><td>{s.payment}</td><td>{s.risk}</td></tr>)}</tbody>
    </table></div><p className="guest-risk">Минимальная цена не означает готовность к закупке. Финальный выбор поставщика остаётся за специалистом после проверки документов и полного базиса доставки.</p>
  </>;
}

function RequestDetail() {
  const { requestId, tab = "overview" } = useParams();
  const r = demoRequests.find((item) => item.id === requestId);
  if (!r) return <div className="panel"><h1>Демонстрационный запрос не найден</h1><Link to="/demo">Вернуться к примерам</Link></div>;
  if (!tabs.some(([key]) => key === tab)) return <Navigate to={`/demo/requests/${r.id}/overview`} replace />;
  return <>
    <Link to="/demo">← Все демонстрационные запросы</Link>
    <div className="guest-heading"><div><p className="note">{r.id} · CAS {r.cas}</p><h1>{r.name}</h1></div><span className="guest-status">{r.status}</span></div>
    <nav className="guest-tabs" aria-label="Вкладки запроса">{tabs.map(([key, label]) => <Link key={key} to={`/demo/requests/${r.id}/${key}`} aria-current={tab === key ? "page" : undefined}>{label}</Link>)}</nav>
    {tab === "overview" && <Overview request={r} />}{tab === "suppliers" && <Suppliers request={r} />}
    {tab === "messages" && <Messages request={r} />}{tab === "comparison" && <Comparison request={r} />}
  </>;
}

export default function GuestWorkspace() {
  const [theme, setTheme] = useState(readTheme);
  return <div className="guest-page">
    <header className="guest-header"><Link className="guest-brand" to="/demo" aria-label="ChemSource AI — демонстрационные запросы"><LogoMark size={30} /><LogoWord /></Link>
      <div className="guest-header-actions"><span>Гость</span><button className="secondary" onClick={() => { const next = theme === "dark" ? "light" : "dark"; applyTheme(next); setTheme(next); }}>{theme === "dark" ? "Светлая тема" : "Тёмная тема"}</button><Link to="/">Выйти из демо</Link></div>
    </header>
    <main className="guest-main"><aside className="guest-banner"><strong>Гостевой просмотр</strong><span>Учебные данные · Только просмотр. Компании, переписка и цены вымышлены.</span></aside>
      <Routes><Route path="/demo" element={<RequestList />} /><Route path="/demo/requests/:requestId" element={<RequestDetail />} /><Route path="/demo/requests/:requestId/:tab" element={<RequestDetail />} /><Route path="*" element={<Navigate to="/demo" replace />} /></Routes>
    </main>
  </div>;
}
