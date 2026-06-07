// Раздел «Запросы»: сводная таблица → карточка запроса / форма нового запроса.
// Полная карточка с вкладками (Верификация → … → История) появится на шаге 3.

import { useState } from "react";
import { api } from "../api/client";
import type { RFQRead } from "../api/types";
import NewRfq from "./NewRfq";
import RequestsTable from "./RequestsTable";
import RfqDetail from "./RfqDetail";

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
      <RfqDetail
        rfq={selected}
        onBack={backToTable}
        onChanged={setSelected}
      />
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
