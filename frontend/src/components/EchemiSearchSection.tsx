import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { createEchemiSearch, getEchemiSearch, listEchemiSearches, userErrorMessage } from "../api/client";
import type { EchemiSearch, EchemiSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import "./echemi.css";
import { EchemiResults } from "./EchemiResults";

const labels: Record<string,string> = {
  queued:"В очереди", running:"Идёт поиск", completed:"Завершён", partial:"Частичный результат",
  blocked:"Проверка Echemi не пройдена", failed:"Ошибка", read:"Карточка прочитана",
  pending:"Ожидает чтения", reading:"Читаем карточку", not_read:"Карточка не прочитана", redirected:"Карточка перенаправлена",
};

export default function EchemiSearchSection() {
  const { searchId } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [query,setQuery] = useState("");
  const [rows,setRows] = useState<EchemiSummary[]>([]);
  const [selected,setSelected] = useState<EchemiSearch | null>(null);
  const [error,setError] = useState("");
  const [loading,setLoading] = useState(true);
  const [sending,setSending] = useState(false);
  const [offset,setOffset] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    setSelected(null);
    setLoading(true);
    setError("");
    async function refresh() {
      try {
        const [history,detail] = await Promise.all([
          listEchemiSearches(offset), searchId ? getEchemiSearch(Number(searchId)) : Promise.resolve(null),
        ]);
        if (!alive) return;
        setRows(history); setSelected(detail); setError("");
      } catch (e) {
        if (alive) setError(userErrorMessage(e,"Не удалось загрузить поиски Echemi."));
      } finally {
        if (alive) { setLoading(false); timer = setTimeout(refresh,5000); }
      }
    }
    void refresh();
    return () => { alive=false; clearTimeout(timer); };
  },[searchId,offset]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (sending || !query.trim()) return;
    setSending(true); setError("");
    try {
      const created = await createEchemiSearch(query.trim());
      setQuery(""); setOffset(0);
      navigate("/echemi/"+created.id);
    } catch (e) {
      setError(userErrorMessage(e,"Не удалось создать поиск."));
    } finally { setSending(false); }
  }
  return <section className="echemi-section">
    <h1>Поиск в Echemi</h1>
    <p>Поиск товаров по названию или CAS. Каждый запуск сохраняется отдельным запросом.</p>
    {user?.role !== "auditor" && <form onSubmit={submit} className="echemi-form">
      <label htmlFor="echemi-query">Название товара или CAS</label>
      <div><input id="echemi-query" value={query} onChange={e=>setQuery(e.target.value)}
        maxLength={200} required placeholder="Например, Aspirin или 50-78-2" />
        <button type="submit" disabled={sending || !query.trim()}>{sending?"Создаём…":"Найти"}</button></div>
      <small>До 10 карточек с первой страницы. Сбор может занять до 15 минут; при появлении CAPTCHA потребуется ваше участие.</small>
    </form>}
    {error && <p role="alert" className="echemi-error">{error}</p>}
    <h2>История запросов</h2>
    {loading && !rows.length ? <p role="status">Загрузка…</p> : !rows.length ? <p>Поисков пока нет.</p> :
      <div className="echemi-table"><table><thead><tr><th>Запрос</th><th>Создан</th><th>Состояние</th><th>Товары</th></tr></thead>
      <tbody>{rows.map(r=><tr key={r.id} className={String(r.id)===searchId?"echemi-selected":""}>
        <td><Link to={"/echemi/"+r.id}>{r.query}</Link></td><td>{new Date(r.created_at).toLocaleString("ru-RU")}</td>
        <td><span className="echemi-history-status">
          {["queued", "running"].includes(r.status) && <span className="loading-spinner" aria-hidden="true" />}
          {labels[r.status] || r.status}
        </span></td><td>{r.result_count}</td></tr>)}</tbody></table></div>}
    <div className="echemi-pages"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Назад</button>
      <button disabled={rows.length<50} onClick={()=>setOffset(offset+50)}>Далее</button></div>
    {selected && <EchemiResults selected={selected} allowVerification={user?.role !== "auditor"} />}
  </section>;
}
