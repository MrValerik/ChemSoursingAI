// Раздел «Запросы» (раздел 6 UI/UX-плана): сводная таблица всех RFQ
// с фильтрами, быстрыми чипами, сортировкой и экспортом CSV.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { RFQListItem } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";
import { Icon, Input, MultiSelect, Toast } from "./ui";

type QuickFilter = "all" | "attention" | "incomplete" | "review";
type ScopeFilter = "mine" | "all";
type SortKey = "id" | "name" | "status" | "n_quotations" | "completeness_pct" | "owner_name";

const NEEDS_ATTENTION = (r: RFQListItem) =>
  r.has_open_escalation ||
  (r.n_quotations > 0 && r.completeness_pct < 100) ||
  r.status === "escalated";

export default function RequestsTable({
  onOpen,
  onNew,
  refreshKey,
}: {
  onOpen: (id: number) => void;
  onNew: () => void;
  refreshKey: number;
}) {
  const { user } = useAuth();
  const showOwner = user?.role === "head" || user?.role === "admin" || user?.role === "auditor";

  const [rows, setRows] = useState<RFQListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<RFQListItem | null>(null);

  const [quick, setQuick] = useState<QuickFilter>("all");
  const [scope, setScope] = useState<ScopeFilter>("mine");
  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [ownerFilters, setOwnerFilters] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("id");
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .listRfqs()
      .then((data) => {
        setRows(data);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const owners = useMemo(
    () => [...new Set(rows.map((r) => r.owner_name).filter((o): o is string => !!o))].sort(),
    [rows],
  );

  const filtered = useMemo(() => {
    let out = rows;
    if (scope === "mine" && user) out = out.filter((r) => r.owner_id === user.id);
    if (quick === "attention") out = out.filter(NEEDS_ATTENTION);
    if (quick === "incomplete")
      out = out.filter((r) => r.n_quotations > 0 && r.completeness_pct < 100);
    if (quick === "review") out = out.filter((r) => r.status === "escalated" || r.has_open_escalation);
    if (statusFilters.length > 0) {
      out = out.filter((r) => statusFilters.includes(r.status));
    }
    if (ownerFilters.length > 0) {
      out = out.filter(
        (r) => r.owner_name !== null && ownerFilters.includes(r.owner_name),
      );
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      out = out.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          // Запрос по аналогу или спецификации живёт без номера.
          (r.cas ?? "").includes(q) ||
          String(r.id).includes(q),
      );
    }
    const dir = sortAsc ? 1 : -1;
    return [...out].sort((a, b) => {
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv), "ru") * dir;
    });
  }, [rows, scope, user, quick, statusFilters, ownerFilters, search, sortKey, sortAsc]);

  const mineCount = user
    ? rows.filter((row) => row.owner_id === user.id).length
    : 0;

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc((v) => !v);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const exportCsv = () => {
    const header = ["№", "Вещество", "CAS", "Статус", "Котировки", "Полные", "Полнота %"];
    if (showOwner) header.push("Ответственный");
    const lines = [header.join(";")];
    for (const r of filtered) {
      const row = [
        r.id,
        `"${r.name.replace(/"/g, '""')}"`,
        r.cas,
        STATUS_LABELS[r.status],
        r.n_quotations,
        r.n_complete,
        r.completeness_pct,
      ];
      if (showOwner) row.push(`"${r.owner_name ?? ""}"`);
      lines.push(row.join(";"));
    }
    const blob = new Blob(["﻿" + lines.join("\r\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `rfq_export_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const arrow = (key: SortKey) => (sortKey === key ? (sortAsc ? " ↑" : " ↓") : "");
  const showDeleteAction = Boolean(user && user.role !== "auditor");

  const canDelete = (request: RFQListItem) =>
    user?.role === "head" ||
    user?.role === "admin" ||
    request.owner_id === user?.id;

  const deleteRequest = async (request: RFQListItem) => {
    setDeletingId(request.id);
    setError(null);
    setNotice(null);
    try {
      await api.deleteRfq(request.id);
      setRows((current) => current.filter((row) => row.id !== request.id));
      setNotice(`Запрос №${request.id} удалён.`);
      setPendingDelete(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="requests-page">
      <div className="requests-header">
        <h1>Запросы</h1>
        <div className="requests-actions">
          <button onClick={onNew}>+ Создать новый запрос</button>
          <button className="secondary" onClick={exportCsv} disabled={filtered.length === 0}>
            Экспорт CSV
          </button>
        </div>
      </div>

      <div className="tabs request-scope-tabs">
        <button
          className={`tab ${scope === "mine" ? "active" : ""}`}
          onClick={() => setScope("mine")}
        >
          Мои запросы <span className="tab-count">{mineCount}</span>
        </button>
        {showOwner && (
          <button
            className={`tab ${scope === "all" ? "active" : ""}`}
            onClick={() => setScope("all")}
          >
            Все запросы <span className="tab-count">{rows.length}</span>
          </button>
        )}
      </div>

      <div className="requests-filter-panel">
        <div className="requests-filters">
          <MultiSelect
            className="requests-status-filter"
            label="Статус"
            options={Object.entries(STATUS_LABELS).map(([value, label]) => ({
              value,
              label,
            }))}
            values={statusFilters}
            onChange={setStatusFilters}
          />
        {showOwner && (
            <MultiSelect
              className="requests-owner-filter"
              label="Ответственный"
              options={owners.map((owner) => ({
                value: owner,
                label: owner,
              }))}
              values={ownerFilters}
              onChange={setOwnerFilters}
            />
        )}
          <Input
            className="filter-search"
            placeholder="Поиск по номеру, веществу или CAS"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="quick-chips">
          {(
            [
              ["all", "Все"],
              ["attention", "Требуют внимания"],
              ["incomplete", "Неполные"],
              ["review", "На ручном разборе"],
            ] as [QuickFilter, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              className={`chip ${quick === key ? "active" : ""}`}
              onClick={() => setQuick(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {notice && <Toast message={notice} onClose={() => setNotice(null)} />}
      {loading && <p className="note">Загрузка…</p>}
      {!loading && filtered.length === 0 && !error && (
        <div className="panel">
          <p className="note">
            {rows.length === 0
              ? "Пока нет запросов — создайте первый."
              : "Под фильтры не попал ни один запрос."}
          </p>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="panel table-panel">
          <table className="summary requests-table">
            <thead>
              <tr>
                <th onClick={() => toggleSort("id")}>№{arrow("id")}</th>
                <th onClick={() => toggleSort("name")}>Вещество / CAS{arrow("name")}</th>
                <th onClick={() => toggleSort("status")}>Статус{arrow("status")}</th>
                <th onClick={() => toggleSort("n_quotations")}>
                  Котировки{arrow("n_quotations")}
                </th>
                <th onClick={() => toggleSort("completeness_pct")}>
                  Полнота{arrow("completeness_pct")}
                </th>
                {showOwner && (
                  <th onClick={() => toggleSort("owner_name")}>
                    Ответственный{arrow("owner_name")}
                  </th>
                )}
                {showDeleteAction && <th className="request-actions-column">Действия</th>}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => onOpen(r.id)}>
                  <td>{r.id}</td>
                  <td>
                    <div>{r.name}</div>
                    <div className="cas">CAS {r.cas}</div>
                  </td>
                  <td>
                    <span className={`badge tone-${STATUS_TONE[r.status]}`}>
                      {STATUS_LABELS[r.status]}
                    </span>
                    {r.has_open_escalation && (
                      <span className="badge tone-warn esc-badge" title="Открытая эскалация">
                        !
                      </span>
                    )}
                  </td>
                  <td>
                    {r.n_quotations > 0 ? `${r.n_complete} / ${r.n_quotations} полн.` : "—"}
                  </td>
                  <td>
                    {r.n_quotations > 0 ? (
                      <div className="meter" title={`${r.completeness_pct}%`}>
                        <div
                          className={`meter-fill ${r.completeness_pct === 100 ? "ok" : "warn"}`}
                          style={{ width: `${r.completeness_pct}%` }}
                        />
                        <span className="meter-label">{r.completeness_pct}%</span>
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  {showOwner && <td>{r.owner_name ?? "—"}</td>}
                  {showDeleteAction && (
                    <td className="request-actions-column">
                      {canDelete(r) && (
                        <button
                          aria-label={`Удалить запрос №${r.id}`}
                          className="ui-icon-button request-delete-button"
                          disabled={deletingId === r.id}
                          title="Удалить запрос"
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setPendingDelete(r);
                          }}
                        >
                          <Icon name="trash" size={16} />
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pendingDelete && (
        <div
          className="request-delete-backdrop"
          role="presentation"
          onClick={() => {
            if (deletingId === null) setPendingDelete(null);
          }}
        >
          <section
            aria-labelledby="request-delete-title"
            aria-modal="true"
            className="request-delete-dialog"
            role="dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="request-delete-dialog-icon" aria-hidden="true">
              <Icon name="trash" size={19} />
            </div>
            <div>
              <h2 id="request-delete-title">Удалить запрос?</h2>
              <p>
                Запрос №{pendingDelete.id} «{pendingDelete.name}» исчезнет из
                рабочих списков, а активный поиск будет остановлен.
              </p>
              <p className="note">
                История поиска, коммуникации и котировки сохранятся для аудита.
              </p>
            </div>
            <div className="request-delete-dialog-actions">
              <button
                className="secondary"
                disabled={deletingId !== null}
                type="button"
                onClick={() => setPendingDelete(null)}
              >
                Отмена
              </button>
              <button
                className="danger"
                disabled={deletingId !== null}
                type="button"
                onClick={() => void deleteRequest(pendingDelete)}
              >
                {deletingId === pendingDelete.id
                  ? "Удаление…"
                  : "Удалить запрос"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
