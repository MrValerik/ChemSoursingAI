// Рабочая область раздела «Запросы» (текущий функционал MVP).
// На шаге 2 будет заменена сводной таблицей всех RFQ с фильтрами.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RFQListItem, RFQRead } from "../api/types";
import NewRfq from "./NewRfq";
import ExtractReplies from "./ExtractReplies";
import Summary from "./Summary";

export default function RfqWorkspace() {
  const [list, setList] = useState<RFQListItem[]>([]);
  const [selected, setSelected] = useState<RFQRead | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refreshList = async () => {
    try {
      setList(await api.listRfqs());
      setListError(null);
    } catch (e) {
      setListError(String(e));
    }
  };

  useEffect(() => {
    void refreshList();
  }, []);

  const onCreated = async (rfq: RFQRead) => {
    setSelected(rfq);
    await refreshList();
  };

  const openRfq = async (id: number) => {
    setSelected(await api.getRfq(id));
  };

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="panel">
          <h2>Запросы</h2>
          {listError && <p className="error">{listError}</p>}
          {list.length === 0 && <p className="note">Пока нет запросов</p>}
          {list.map((r) => (
            <div
              key={r.id}
              className="rfq-list-item"
              onClick={() => void openRfq(r.id)}
            >
              <div>
                #{r.id} · {r.name}{" "}
                <span className={`badge ${r.verified ? "ok" : "muted"}`}>
                  {r.status}
                </span>
              </div>
              <div className="cas">CAS {r.cas}</div>
            </div>
          ))}
        </div>
      </aside>

      <main className="main">
        <NewRfq onCreated={onCreated} />

        {selected && (
          <div className="panel">
            <h2>
              RFQ #{selected.id} · {selected.name}{" "}
              <span className={`badge ${selected.verified ? "ok" : "muted"}`}>
                {selected.status}
              </span>
            </h2>
            <div className="note">
              CAS {selected.cas}
              {selected.verification?.molecular_formula
                ? ` · ${selected.verification.molecular_formula}`
                : ""}{" "}
              · базисы: {(selected.incoterms ?? []).join(", ")}
            </div>
            {selected.rfq_body && (
              <pre className="letter" style={{ marginTop: 12 }}>
                {selected.rfq_body}
              </pre>
            )}
          </div>
        )}

        {selected && (
          <ExtractReplies
            rfqId={selected.id}
            onStored={() => {
              setRefreshKey((k) => k + 1);
              void refreshList();
            }}
          />
        )}

        {selected && <Summary rfqId={selected.id} refreshKey={refreshKey} />}
      </main>
    </div>
  );
}
