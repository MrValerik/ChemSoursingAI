// Раздел «Запросы»: сводная таблица → карточка запроса / форма нового запроса.
// Что показать, решает адрес: /requests, /requests/new, /requests/:rfqId[/:tab].

import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, userErrorMessage } from "../api/client";
import type { RFQRead } from "../api/types";
import NewRfq from "./NewRfq";
import RequestsTable from "./RequestsTable";
import RfqBatchSummary from "./RfqBatchSummary";
import RfqDetail from "./RfqDetail";

export default function RfqWorkspace() {
  const { rfqId, batchId } = useParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<RFQRead | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isNew = pathname === "/requests/new";
  const openedBatch = batchId ? Number(batchId) : null;
  const openedId = !isNew && rfqId ? Number(rfqId) : null;

  useEffect(() => {
    if (openedId === null) {
      setSelected(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rfq = await api.getRfq(openedId);
        if (!cancelled) {
          setSelected(rfq);
          setError(null);
        }
      } catch (e) {
        // Адрес мог прийти из старой закладки на удалённый запрос.
        if (!cancelled) setError(userErrorMessage(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [openedId]);

  const backToTable = () => navigate("/requests");

  if (isNew) {
    return (
      <div className="requests-page">
        <button className="secondary back-btn" onClick={backToTable}>
          ← К запросам
        </button>
        <NewRfq
          onCreated={(rfq) => navigate(`/requests/${rfq.id}`)}
          onBatchCreated={(id) => navigate(`/requests/batch/${id}`)}
        />
      </div>
    );
  }

  if (openedBatch !== null) {
    return (
      <RfqBatchSummary
        batchId={openedBatch}
        onOpenRfq={(id) => navigate(`/requests/${id}`)}
        onBack={backToTable}
      />
    );
  }

  if (openedId !== null) {
    if (error) {
      return (
        <div className="requests-page">
          <button className="secondary back-btn" onClick={backToTable}>
            ← К запросам
          </button>
          <p className="error">{error}</p>
        </div>
      );
    }
    if (!selected) return <p className="note" style={{ padding: 24 }}>Загрузка…</p>;
    return (
      <RfqDetail
        rfq={selected}
        onBack={backToTable}
        onChanged={setSelected}
        onOpenSubstance={(id) => navigate(`/substances/${id}`)}
      />
    );
  }

  return (
    <>
      {error && <p className="error" style={{ padding: "0 24px" }}>{error}</p>}
      <RequestsTable
        onOpen={(id) => navigate(`/requests/${id}`)}
        onNew={() => navigate("/requests/new")}
      />
    </>
  );
}
