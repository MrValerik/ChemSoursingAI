import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getEchemiSearch, listEchemiSearches, startRfqEchemiSearch, userErrorMessage } from "../api/client";
import type { EchemiSearch, EchemiSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { EchemiResults } from "./EchemiResults";

export default function RfqEchemiResults({ rfqId }: { rfqId: number }) {
  const { user } = useAuth();
  const [rows, setRows] = useState<EchemiSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<EchemiSearch | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const canSearch = user?.role !== "auditor";
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const history = await listEchemiSearches(0, rfqId);
        const id = selectedId ?? history[0]?.id;
        const detail = id ? await getEchemiSearch(id) : null;
        if (!alive) return;
        setRows(history); setSelected(detail); setError("");
      } catch (e) {
        if (alive) setError(userErrorMessage(e, "Не удалось загрузить результаты Echemi."));
      } finally {
        if (alive) { setLoading(false); timer = setTimeout(refresh, 5000); }
      }
    }
    void refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [rfqId, selectedId, refreshKey]);
  async function start() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const row = await startRfqEchemiSearch(rfqId);
      setSelectedId(null); setSelected(row); setRefreshKey(value => value + 1);
    } catch (e) {
      setError(userErrorMessage(e, "Не удалось запустить Echemi."));
    } finally { setBusy(false); }
  }
  const active = rows.some(row => ["queued", "running"].includes(row.status));
  return <section className="panel rfq-echemi" aria-labelledby="rfq-echemi-heading">
    <h2 id="rfq-echemi-heading">Найденные компании — Echemi</h2>
    <p className="muted">Поиск на площадке запускается вместе с поиском компаний по CAS или названию вещества.
      Данные Echemi собираются отдельно от проверки производителей и обновляются по мере чтения карточек.</p>
    {error && <p role="alert" className="echemi-error">{error}</p>}
    {loading && <p role="status">Загружаем результаты Echemi…</p>}
    <div className="echemi-pages">
      {canSearch && <button onClick={start} disabled={busy || active || loading}>
        {busy ? "Создаём поиск…" : active ? "Поиск Echemi выполняется" : rows.length ? "Повторить поиск Echemi" : "Искать в Echemi"}
      </button>}
      {rows.length > 1 && <label>Запуск Echemi{" "}
        <select value={selectedId ?? ""} onChange={e => { setSelected(null); setSelectedId(e.target.value ? Number(e.target.value) : null); }}>
          <option value="">Последний запуск</option>
          {rows.map(row => <option key={row.id} value={row.id}>№{row.id} · {row.query} · {new Date(row.created_at).toLocaleString("ru-RU")}</option>)}
        </select>
      </label>}
      {selected && <Link to={"/echemi/" + selected.id}>Открыть в истории Echemi</Link>}
    </div>
    {!loading && !error && !selected && <p>Поиск Echemi для этого запроса ещё не запускался.</p>}
    {selected && <EchemiResults selected={selected} allowVerification={canSearch} />}
  </section>;
}
