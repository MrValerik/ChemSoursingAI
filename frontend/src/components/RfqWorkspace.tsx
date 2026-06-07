// Раздел «Запросы»: сводная таблица → карточка запроса / форма нового запроса.
// Полная карточка с вкладками (Верификация → … → История) появится на шаге 3.

import { useState } from "react";
import { api } from "../api/client";
import type { RFQRead } from "../api/types";
import NewRfq from "./NewRfq";
import ExtractReplies from "./ExtractReplies";
import RequestsTable from "./RequestsTable";
import Summary from "./Summary";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";

type View = "table" | "new" | "detail";

export default function RfqWorkspace() {
  const [view, setView] = useState<View>("table");
  const [selected, setSelected] = useState<RFQRead | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const openRfq = async (id: number) => {
    try {
      setSelected(await api.getRfq(id));
      setView("detail");
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  const backToTable = () => {
    setView("table");
    setSelected(null);
    setRefreshKey((k) => k + 1);
  };

  if (view === "new") {
    return (
      <div className="requests-page">
        <button className="secondary back-btn" onClick={backToTable}>
          ← К запросам
        </button>
        <NewRfq
          onCreated={(rfq) => {
            setSelected(rfq);
            setView("detail");
          }}
        />
      </div>
    );
  }

  if (view === "detail" && selected) {
    return (
      <div className="requests-page">
        <div className="detail-header">
          <button className="secondary back-btn" onClick={backToTable}>
            ← К запросам
          </button>
          <h1>
            RFQ #{selected.id} · {selected.name}
          </h1>
          <span className={`badge tone-${STATUS_TONE[selected.status]}`}>
            {STATUS_LABELS[selected.status]}
          </span>
        </div>

        <div className="panel">
          <div className="note">
            CAS {selected.cas}
            {selected.verification?.molecular_formula
              ? ` · ${selected.verification.molecular_formula}`
              : ""}{" "}
            · базисы: {(selected.incoterms ?? []).join(", ")}
            {selected.owner_name ? ` · ответственный: ${selected.owner_name}` : ""}
          </div>
          {selected.rfq_body && (
            <pre className="letter" style={{ marginTop: 12 }}>
              {selected.rfq_body}
            </pre>
          )}
        </div>

        <ExtractReplies
          rfqId={selected.id}
          onStored={() => setRefreshKey((k) => k + 1)}
        />
        <Summary rfqId={selected.id} refreshKey={refreshKey} />
      </div>
    );
  }

  return (
    <>
      {error && <p className="error" style={{ padding: "0 24px" }}>{error}</p>}
      <RequestsTable
        refreshKey={refreshKey}
        onOpen={(id) => void openRfq(id)}
        onNew={() => setView("new")}
      />
    </>
  );
}
