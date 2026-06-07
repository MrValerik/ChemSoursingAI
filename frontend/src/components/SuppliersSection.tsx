// Раздел «Поставщики» (раздел 14 UI/UX-плана): реестр и карточка поставщика.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SupplierRead } from "../api/types";

const TYPE_LABELS: Record<string, string> = {
  manufacturer: "производитель",
  distributor: "дистрибьютор",
};

export default function SuppliersSection() {
  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [selected, setSelected] = useState<SupplierRead | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listSuppliers()
      .then(setSuppliers)
      .catch((e) => setError(String(e)));
  }, []);

  const filtered = suppliers.filter((s) =>
    s.company.toLowerCase().includes(search.trim().toLowerCase()),
  );

  return (
    <div className="requests-page">
      <div className="requests-header">
        <h1>Поставщики</h1>
        <input
          className="filter-search"
          placeholder="Поиск по компании…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {error && <p className="error">{error}</p>}

      <div className="suppliers-layout">
        <div className="panel table-panel suppliers-list">
          <table className="summary requests-table">
            <thead>
              <tr>
                <th>Компания</th>
                <th>Тип</th>
                <th>Страна</th>
                <th>Репутация</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr
                  key={s.id}
                  className={`clickable ${selected?.id === s.id ? "row-active" : ""}`}
                  onClick={() => setSelected(s)}
                >
                  <td>{s.company}</td>
                  <td>{s.type ? TYPE_LABELS[s.type] : "—"}</td>
                  <td>{s.country ?? "—"}</td>
                  <td>{s.reputation ? "★".repeat(Number(s.reputation) || 0) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && (
          <aside className="panel supplier-card">
            <h2>{selected.company}</h2>
            <dl className="params-list">
              <dt>Тип</dt>
              <dd>{selected.type ? TYPE_LABELS[selected.type] : "—"}</dd>
              <dt>Страна</dt>
              <dd>{selected.country ?? "—"}</dd>
              <dt>Источник</dt>
              <dd>{selected.source ?? "—"}</dd>
              <dt>Сертификаты</dt>
              <dd>{selected.certificates?.join(", ") ?? "—"}</dd>
              <dt>Каналы</dt>
              <dd>{selected.channels.join(", ") || "нет контактов"}</dd>
            </dl>
            <p className="note" style={{ marginTop: 12 }}>
              Карточка менеджера с историей переписки и связанными запросами —
              этап интеграций (функция 8 ТЗ).
            </p>
          </aside>
        )}
      </div>
    </div>
  );
}
