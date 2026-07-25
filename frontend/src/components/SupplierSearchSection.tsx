import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { SupplierSearchResponse } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export default function SupplierSearchSection() {
  const { user } = useAuth();
  const [cas, setCas] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("China");
  const [instructions, setInstructions] = useState("");
  const [data, setData] = useState<SupplierSearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    setBusy(true);
    setError(null);
    try {
      setData(
        await api.searchSuppliers({
          cas,
          name,
          country: country || null,
          additional_instructions: instructions || null,
        }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const add = async (title: string, url: string) => {
    try {
      await api.addSupplier({
        company: title.slice(0, 255),
        country: country || null,
        source: url,
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className="requests-page">
      <div className="requests-header">
        <div>
          <h1>Поиск поставщиков</h1>
          <p className="note">Qwen формирует запрос, а найденные факты сохраняются со ссылками.</p>
        </div>
      </div>
      <div className="panel">
        <div className="row">
          <div className="field"><label>CAS</label><input value={cas} onChange={(e) => setCas(e.target.value)} /></div>
          <div className="field" style={{ flex: 2 }}><label>Вещество</label><input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="field"><label>Страна</label><input value={country} onChange={(e) => setCountry(e.target.value)} /></div>
        </div>
        <div className="field">
          <label>Дополнительный поисковый промпт</label>
          <textarea
            rows={3}
            maxLength={4000}
            placeholder="Например: только производители фармацевтического грейда с GMP"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>
        <button disabled={busy || !cas.trim() || !name.trim()} onClick={() => void search()}>
          {busy ? "Qwen и поиск работают…" : "Найти поставщиков"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <div className="panel">
          <div className="note">Запрос: {data.query} · Qwen: {data.ai_used ? "да" : "fallback"}</div>
          <p className="note">{data.warning}</p>
          {data.results.map((result) => (
            <div className="rfq-list-item" key={result.url}>
              <div style={{ flex: 1 }}>
                <a href={result.url} target="_blank" rel="noreferrer">{result.title}</a>
                <div className="note">{result.snippet}</div>
                <div className="cas">{result.url}</div>
              </div>
              {user?.role !== "auditor" && (
                <button className="secondary" onClick={() => void add(result.title, result.url)}>
                  Добавить кандидата
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
