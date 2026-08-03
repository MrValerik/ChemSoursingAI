// Глобальный реестр компаний. Поиск по веществу запускается только из карточки
// запроса; этот раздел предназначен для фильтрации и повторного использования
// уже известных поставщиков и кандидатов.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  ChannelKind,
  SupplierQualificationStatus,
  SupplierRead,
  SupplierTypeKind,
} from "../api/types";
import { Select } from "./ui";

const TYPE_LABELS: Record<SupplierTypeKind, string> = {
  manufacturer: "Производитель",
  distributor: "Дистрибьютор",
};

const STATUS_LABELS: Record<SupplierQualificationStatus, string> = {
  candidate: "Кандидат",
  under_review: "На проверке",
  verified: "Проверен",
  rejected: "Отклонён",
};

const STATUS_TONES: Record<SupplierQualificationStatus, string> = {
  candidate: "tone-neutral",
  under_review: "tone-warn",
  verified: "tone-ok",
  rejected: "tone-danger",
};

type SortKey =
  | "company"
  | "country"
  | "type"
  | "qualification_status"
  | "evidence_score"
  | "request_count"
  | "last_checked_at";

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "Не проверялся";

export default function SuppliersSection({
  onOpenRfq,
}: {
  onOpenRfq: (id: number) => void;
}) {
  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [selected, setSelected] = useState<SupplierRead | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [documentFilter, setDocumentFilter] = useState("");
  const [minimumScore, setMinimumScore] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("company");
  const [sortAsc, setSortAsc] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listSuppliers()
      .then((items) => {
        setSuppliers(items);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const countries = useMemo(
    () =>
      [...new Set(suppliers.map((supplier) => supplier.country).filter(Boolean))]
        .sort((a, b) => String(a).localeCompare(String(b), "ru")) as string[],
    [suppliers],
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("ru");
    const score = minimumScore ? Number(minimumScore) : null;
    const rows = suppliers.filter((supplier) => {
      const searchable = [
        supplier.company,
        supplier.country,
        supplier.source,
        ...(supplier.certificates ?? []),
        ...supplier.linked_requests.flatMap((request) => [
          request.name,
          request.cas,
          String(request.rfq_id),
        ]),
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("ru");
      if (query && !searchable.includes(query)) return false;
      if (typeFilter && supplier.type !== typeFilter) return false;
      if (statusFilter && supplier.qualification_status !== statusFilter) return false;
      if (countryFilter && supplier.country !== countryFilter) return false;
      if (
        channelFilter &&
        !supplier.channels.includes(channelFilter as ChannelKind)
      ) {
        return false;
      }
      if (documentFilter === "coa" && !supplier.has_coa) return false;
      if (documentFilter === "tds" && !supplier.has_tds) return false;
      if (
        documentFilter === "certificates" &&
        (supplier.certificates?.length ?? 0) === 0
      ) {
        return false;
      }
      if (score !== null && (supplier.evidence_score ?? -1) < score) return false;
      return true;
    });
    const direction = sortAsc ? 1 : -1;
    return [...rows].sort((left, right) => {
      const a = left[sortKey] ?? "";
      const b = right[sortKey] ?? "";
      if (typeof a === "number" && typeof b === "number") {
        return (a - b) * direction;
      }
      return String(a).localeCompare(String(b), "ru") * direction;
    });
  }, [
    channelFilter,
    countryFilter,
    documentFilter,
    minimumScore,
    search,
    sortAsc,
    sortKey,
    statusFilter,
    suppliers,
    typeFilter,
  ]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((value) => !value);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };
  const arrow = (key: SortKey) =>
    sortKey === key ? (sortAsc ? " ↑" : " ↓") : "";

  return (
    <div className="requests-page suppliers-page">
      <div className="requests-header">
        <div>
          <h1>Поставщики</h1>
          <p className="note">
            Единый каталог компаний, найденных и добавленных во всех запросах.
          </p>
        </div>
      </div>

      <div className="requests-filters supplier-filters">
        <input
          className="filter-search"
          placeholder="Компания, страна, сертификат или запрос (название, CAS)"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
          <option value="">Тип: любой</option>
          <option value="manufacturer">Производитель</option>
          <option value="distributor">Дистрибьютор</option>
        </Select>
        <Select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
        >
          <option value="">Статус: любой</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          value={countryFilter}
          onChange={(event) => setCountryFilter(event.target.value)}
        >
          <option value="">Страна: любая</option>
          {countries.map((country) => (
            <option value={country} key={country}>
              {country}
            </option>
          ))}
        </Select>
        <Select
          value={channelFilter}
          onChange={(event) => setChannelFilter(event.target.value)}
        >
          <option value="">Контакт: любой</option>
          <option value="email">Email</option>
          <option value="whatsapp">WhatsApp</option>
        </Select>
        <Select
          value={documentFilter}
          onChange={(event) => setDocumentFilter(event.target.value)}
        >
          <option value="">Документы: любые</option>
          <option value="coa">Есть CoA в котировке</option>
          <option value="tds">Есть TDS в котировке</option>
          <option value="certificates">Указаны сертификаты</option>
        </Select>
        <Select
          value={minimumScore}
          onChange={(event) => setMinimumScore(event.target.value)}
        >
          <option value="">Баллы: любые</option>
          <option value="80">80 и выше</option>
          <option value="60">60 и выше</option>
          <option value="40">40 и выше</option>
        </Select>
      </div>

      {error && <p className="error">{error}</p>}
      {!error && filtered.length === 0 && (
        <div className="panel">
          <p className="note">
            {suppliers.length === 0
              ? "Реестр пока пуст. Кандидатов можно добавить из поиска внутри запроса."
              : "Под выбранные фильтры поставщики не найдены."}
          </p>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="suppliers-layout">
          <div className="panel table-panel data-table suppliers-list">
            <table className="summary requests-table">
              <thead>
                <tr>
                  <th onClick={() => toggleSort("company")}>
                    Компания{arrow("company")}
                  </th>
                  <th onClick={() => toggleSort("qualification_status")}>
                    Статус{arrow("qualification_status")}
                  </th>
                  <th onClick={() => toggleSort("type")}>Тип{arrow("type")}</th>
                  <th onClick={() => toggleSort("country")}>
                    Страна{arrow("country")}
                  </th>
                  <th className="col-num" onClick={() => toggleSort("evidence_score")}>
                    Балл{arrow("evidence_score")}
                  </th>
                  <th className="col-num" onClick={() => toggleSort("request_count")}>
                    Запросы{arrow("request_count")}
                  </th>
                  <th>Контакты</th>
                  <th onClick={() => toggleSort("last_checked_at")}>
                    Проверка{arrow("last_checked_at")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((supplier) => (
                  <tr
                    key={supplier.id}
                    className={`clickable ${
                      selected?.id === supplier.id ? "row-active" : ""
                    }`}
                    onClick={() => setSelected(supplier)}
                  >
                    <td>
                      <strong>{supplier.company}</strong>
                      {/* Источник — единственное поле переменной длины: обрезаем,
                          чтобы строки держали общую высоту. */}
                      <div
                        className="cas supplier-source"
                        title={supplier.source ?? undefined}
                      >
                        {supplier.source ?? "Источник не указан"}
                      </div>
                    </td>
                    <td>
                      <span
                        className={`badge ${
                          STATUS_TONES[supplier.qualification_status]
                        }`}
                      >
                        {STATUS_LABELS[supplier.qualification_status]}
                      </span>
                    </td>
                    <td>{supplier.type ? TYPE_LABELS[supplier.type] : "Не определён"}</td>
                    <td>{supplier.country ?? "—"}</td>
                    <td className="col-num">{supplier.evidence_score ?? "—"}</td>
                    <td className="col-num">{supplier.request_count}</td>
                    <td>{supplier.channels.join(", ") || "Нет"}</td>
                    <td>{formatDate(supplier.last_checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && (
            <aside className="panel supplier-card">
              <h2>{selected.company}</h2>
              <span
                className={`badge ${STATUS_TONES[selected.qualification_status]}`}
              >
                {STATUS_LABELS[selected.qualification_status]}
              </span>
              <dl className="params-list supplier-details">
                <dt>Тип</dt>
                <dd>{selected.type ? TYPE_LABELS[selected.type] : "Не определён"}</dd>
                <dt>Страна</dt>
                <dd>{selected.country ?? "—"}</dd>
                <dt>Проверяемый балл</dt>
                <dd>{selected.evidence_score ?? "Нет оценки"}</dd>
                <dt>Последняя проверка</dt>
                <dd>{formatDate(selected.last_checked_at)}</dd>
                <dt>Источник</dt>
                <dd>
                  {selected.source?.startsWith("http") ? (
                    <a href={selected.source} target="_blank" rel="noreferrer">
                      Открыть источник
                    </a>
                  ) : (
                    selected.source ?? "—"
                  )}
                </dd>
                <dt>Сертификаты</dt>
                <dd>{selected.certificates?.join(", ") || "Не указаны"}</dd>
                <dt>Документы в ответах</dt>
                <dd>
                  {[
                    selected.has_coa ? "CoA" : null,
                    selected.has_tds ? "TDS" : null,
                  ]
                    .filter(Boolean)
                    .join(", ") || "Не получены"}
                </dd>
                <dt>Каналы</dt>
                <dd>{selected.channels.join(", ") || "Нет контактов"}</dd>
                <dt>Контактов</dt>
                <dd>{selected.contacts_count}</dd>
              </dl>

              <h3>Связанные запросы</h3>
              {selected.linked_requests.length === 0 ? (
                <p className="note">Компания ещё не выбрана ни для одного запроса.</p>
              ) : (
                <div className="supplier-request-links">
                  {selected.linked_requests.map((request) => (
                    <button
                      className="secondary"
                      key={request.rfq_id}
                      onClick={() => onOpenRfq(request.rfq_id)}
                    >
                      #{request.rfq_id} · {request.name} · CAS {request.cas}
                    </button>
                  ))}
                </div>
              )}
              <p className="note">
                Статус производителя и итоговый выбор подтверждает человек.
              </p>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
