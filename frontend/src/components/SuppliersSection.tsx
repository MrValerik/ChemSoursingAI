// Глобальный реестр компаний. Поиск по веществу запускается только из карточки
// запроса; этот раздел предназначен для фильтрации и повторного использования
// уже известных поставщиков и кандидатов.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, userErrorMessage } from "../api/client";
import type {
  ChannelKind,
  PurchaseHistoryEntry,
  SupplierQualificationStatus,
  SupplierRead,
  SupplierTypeKind,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Icon, Input, Select } from "./ui";

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
  | "verified_by_name"
  | "last_checked_at";

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("ru-RU") : "Не проверялся";

interface SupplierForm {
  id: number | null;
  company: string;
  type: "" | SupplierTypeKind;
  country: string;
  source: string;
  reputation: string;
  qualification_status: SupplierQualificationStatus;
  evidence_score: string;
  certificates: string;
  email: string;
  whatsapp: string;
}

const EMPTY_FORM: SupplierForm = {
  id: null,
  company: "",
  type: "",
  country: "",
  source: "",
  reputation: "",
  qualification_status: "candidate",
  evidence_score: "",
  certificates: "",
  email: "",
  whatsapp: "",
};

export default function SuppliersSection() {
  const { user } = useAuth();
  const canEdit = user?.role !== "auditor";
  const navigate = useNavigate();
  const onOpenRfq = (id: number) => navigate(`/requests/${id}`);
  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [selected, setSelected] = useState<SupplierRead | null>(null);
  const [purchaseHistory, setPurchaseHistory] = useState<PurchaseHistoryEntry[]>([]);
  const [purchaseHistoryLoading, setPurchaseHistoryLoading] = useState(false);
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
  const [form, setForm] = useState<SupplierForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const [busySupplierId, setBusySupplierId] = useState<number | null>(null);

  useEffect(() => {
    if (menuFor === null) return;
    const close = () => setMenuFor(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuFor]);

  const load = useCallback(async () => {
    try {
      const items = await api.listSuppliers();
        setSuppliers(items);
        setSelected((current) =>
          current ? items.find((item) => item.id === current.id) ?? null : null,
        );
        setError(null);
    } catch (caught) {
      setError(userErrorMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) {
      setPurchaseHistory([]);
      return;
    }
    let active = true;
    setPurchaseHistoryLoading(true);
    api
      .listSupplierPurchaseHistory(selected.id)
      .then((items) => {
        if (active) setPurchaseHistory(items);
      })
      .catch(() => {
        if (active) setPurchaseHistory([]);
      })
      .finally(() => {
        if (active) setPurchaseHistoryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected]);

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
        supplier.verified_by_name,
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

  const beginEdit = (supplier: SupplierRead) => {
    setForm({
      id: supplier.id,
      company: supplier.company,
      type: supplier.type ?? "",
      country: supplier.country ?? "",
      source: supplier.source ?? "",
      reputation: supplier.reputation ?? "",
      qualification_status: supplier.qualification_status,
      evidence_score: supplier.evidence_score?.toString() ?? "",
      certificates: supplier.certificates?.join(", ") ?? "",
      email: "",
      whatsapp: "",
    });
    setSelected(supplier);
    setError(null);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form?.company.trim()) return;
    const certificates = form.certificates
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const payload = {
      company: form.company.trim(),
      type: form.type || null,
      country: form.country.trim() || null,
      source: form.source.trim() || null,
      reputation: form.reputation.trim() || null,
      qualification_status: form.qualification_status,
      evidence_score: form.evidence_score === "" ? null : Number(form.evidence_score),
      certificates: certificates.length ? certificates : null,
    };
    setSaving(true);
    try {
      const saved = form.id === null
        ? await api.addSupplier({
            ...payload,
            email: form.email.trim() || null,
            whatsapp: form.whatsapp.trim() || null,
          })
        : await api.updateSupplier(form.id, payload);
      setForm(null);
      setSelected(saved);
      await load();
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (supplier: SupplierRead) => {
    setMenuFor(null);
    if (!window.confirm(`Удалить «${supplier.company}» из реестра поставщиков?`)) {
      return;
    }
    try {
      await api.deleteSupplier(supplier.id);
      if (selected?.id === supplier.id) setSelected(null);
      setForm((current) => (current?.id === supplier.id ? null : current));
      await load();
    } catch (caught) {
      setError(userErrorMessage(caught));
    }
  };

  const changeStatus = async (
    supplier: SupplierRead,
    status: SupplierQualificationStatus,
  ) => {
    if (busySupplierId !== null || supplier.qualification_status === status) return;
    setMenuFor(null);
    if (
      status === "rejected" &&
      !window.confirm(
        "Исключить компанию из реестра? Она перестанет предлагаться во всех запросах.",
      )
    ) {
      return;
    }
    setBusySupplierId(supplier.id);
    setError(null);
    try {
      await api.setSupplierQualification(supplier.id, status);
      setForm((current) =>
        current?.id === supplier.id
          ? { ...current, qualification_status: status }
          : current,
      );
      await load();
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setBusySupplierId(null);
    }
  };

  return (
    <div className="requests-page suppliers-page">
      <div className="requests-header">
        <div>
          <h1>Поставщики</h1>
          <p className="note">
            Единый каталог компаний, найденных и добавленных во всех запросах.
          </p>
        </div>
        {canEdit && (
          <button
            className="secondary button-with-icon"
            type="button"
            onClick={() => {
              setForm({ ...EMPTY_FORM });
              setSelected(null);
              setError(null);
            }}
          >
            <Icon name="edit" size={17} />
            Добавить поставщика
          </button>
        )}
      </div>

      {canEdit && form && (
        <form className="panel supplier-registry-editor" onSubmit={save}>
          <div className="supplier-registry-editor-header">
            <h2>{form.id === null ? "Новый поставщик" : "Изменение поставщика"}</h2>
            <button className="secondary btn-small" type="button" onClick={() => setForm(null)}>
              Отмена
            </button>
          </div>
          <div className="supplier-registry-form-grid">
            <Input
              aria-label="Название компании"
              placeholder="Название компании"
              value={form.company}
              onChange={(event) => setForm({ ...form, company: event.target.value })}
            />
            <Select
              ariaLabel="Тип поставщика"
              value={form.type}
              onChange={(value) => setForm({ ...form, type: value as SupplierForm["type"] })}
              options={[
                { value: "", label: "Тип не определён" },
                { value: "manufacturer", label: "Производитель" },
                { value: "distributor", label: "Дистрибьютор" },
              ]}
            />
            <Input
              aria-label="Страна поставщика"
              placeholder="Страна"
              value={form.country}
              onChange={(event) => setForm({ ...form, country: event.target.value })}
            />
            <Input
              aria-label="Источник сведений"
              placeholder="Источник или URL"
              value={form.source}
              onChange={(event) => setForm({ ...form, source: event.target.value })}
            />
            <Select
              ariaLabel="Статус квалификации"
              value={form.qualification_status}
              onChange={(value) =>
                setForm({
                  ...form,
                  qualification_status: value as SupplierQualificationStatus,
                })
              }
              options={Object.entries(STATUS_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
            <Input
              aria-label="Проверяемый балл"
              min="0"
              max="100"
              placeholder="Балл 0–100"
              type="number"
              value={form.evidence_score}
              onChange={(event) => setForm({ ...form, evidence_score: event.target.value })}
            />
            <Input
              aria-label="Сертификаты"
              placeholder="Сертификаты через запятую"
              value={form.certificates}
              onChange={(event) => setForm({ ...form, certificates: event.target.value })}
            />
            <Input
              aria-label="Комментарий о репутации"
              placeholder="Комментарий о репутации"
              value={form.reputation}
              onChange={(event) => setForm({ ...form, reputation: event.target.value })}
            />
            {form.id === null && (
              <>
                <Input
                  aria-label="Email поставщика"
                  placeholder="Email (необязательно)"
                  type="email"
                  value={form.email}
                  onChange={(event) => setForm({ ...form, email: event.target.value })}
                />
                <Input
                  aria-label="WhatsApp поставщика"
                  placeholder="WhatsApp (необязательно)"
                  value={form.whatsapp}
                  onChange={(event) => setForm({ ...form, whatsapp: event.target.value })}
                />
              </>
            )}
          </div>
          <div className="actions">
            <button disabled={saving || !form.company.trim()} type="submit">
              {saving ? "Сохранение…" : "Сохранить"}
            </button>
          </div>
        </form>
      )}

      <div className="requests-filters supplier-filters">
        <input
          className="filter-search"
          placeholder="Компания, страна, сертификат или запрос (название, CAS)"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          value={typeFilter}
          onChange={setTypeFilter}
          options={[
            { value: "", label: "Тип: любой" },
            { value: "manufacturer", label: "Производитель" },
            { value: "distributor", label: "Дистрибьютор" },
          ]}
        />
        <Select
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "", label: "Статус: любой" },
            ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
          ]}
        />
        <Select
          value={countryFilter}
          onChange={setCountryFilter}
          options={[
            { value: "", label: "Страна: любая" },
            ...countries.map((country) => ({ value: country, label: country })),
          ]}
        />
        <Select
          value={channelFilter}
          onChange={setChannelFilter}
          options={[
            { value: "", label: "Контакт: любой" },
            { value: "email", label: "Email" },
            { value: "whatsapp", label: "WhatsApp" },
          ]}
        />
        <Select
          value={documentFilter}
          onChange={setDocumentFilter}
          options={[
            { value: "", label: "Документы: любые" },
            { value: "coa", label: "Есть CoA в котировке" },
            { value: "tds", label: "Есть TDS в котировке" },
            { value: "certificates", label: "Указаны сертификаты" },
          ]}
        />
        <Select
          value={minimumScore}
          onChange={setMinimumScore}
          options={[
            { value: "", label: "Баллы: любые" },
            { value: "80", label: "80 и выше" },
            { value: "60", label: "60 и выше" },
            { value: "40", label: "40 и выше" },
          ]}
        />
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
                  <th onClick={() => toggleSort("verified_by_name")}>
                    Кто подтвердил{arrow("verified_by_name")}
                  </th>
                  <th onClick={() => toggleSort("last_checked_at")}>
                    Проверка{arrow("last_checked_at")}
                  </th>
                  {canEdit && <th className="qualification-actions-column" />}
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
                    <td>
                      {supplier.country ?? "—"}
                      {supplier.icp_licence && (
                        <a
                          className="supplier-licence"
                          href="https://beian.miit.gov.cn/"
                          target="_blank"
                          rel="noreferrer noopener"
                          title={
                            "Номер лицензии сайта в материковом Китае: " +
                            supplier.icp_licence +
                            ". Сверьте его на beian.miit.gov.cn — это запись " +
                            "министерства, а не заявление продавца о себе."
                          }
                        >
                          {supplier.icp_licence}
                        </a>
                      )}
                    </td>
                    <td className="col-num">{supplier.evidence_score ?? "—"}</td>
                    <td className="col-num">{supplier.request_count}</td>
                    <td>{supplier.channels.join(", ") || "Нет"}</td>
                    <td>{supplier.verified_by_name ?? "Не подтверждён"}</td>
                    <td>{formatDate(supplier.last_checked_at)}</td>
                    {canEdit && (
                      <td
                        className="qualification-actions-column"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <div className="row-menu">
                          <button
                            aria-label={`Действия с компанией ${supplier.company}`}
                            className="ui-icon-button row-menu-button"
                            disabled={busySupplierId === supplier.id}
                            title="Действия с компанией"
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setMenuFor((current) =>
                                current === supplier.id ? null : supplier.id,
                              );
                            }}
                          >
                            ⋮
                          </button>
                          {menuFor === supplier.id && (
                            <div className="dropdown row-menu-dropdown">
                              <div className="dropdown-title">{supplier.company}</div>
                              {supplier.qualification_status !== "verified" && (
                                <button
                                  className="dropdown-item"
                                  type="button"
                                  onClick={() => void changeStatus(supplier, "verified")}
                                >
                                  Подтвердить поставщика
                                </button>
                              )}
                              {supplier.qualification_status !== "under_review" && (
                                <button
                                  className="dropdown-item"
                                  type="button"
                                  onClick={() => void changeStatus(supplier, "under_review")}
                                >
                                  Отправить на проверку
                                </button>
                              )}
                              {supplier.qualification_status !== "candidate" && (
                                <button
                                  className="dropdown-item"
                                  type="button"
                                  onClick={() => void changeStatus(supplier, "candidate")}
                                >
                                  Вернуть в кандидаты
                                </button>
                              )}
                              {supplier.qualification_status !== "rejected" && (
                                <button
                                  className="dropdown-item is-danger"
                                  type="button"
                                  onClick={() => void changeStatus(supplier, "rejected")}
                                >
                                  Исключить из реестра
                                </button>
                              )}
                              <button
                                className="dropdown-item"
                                type="button"
                                onClick={() => {
                                  setMenuFor(null);
                                  beginEdit(supplier);
                                }}
                              >
                                Изменить данные
                              </button>
                              <button
                                className="dropdown-item is-danger"
                                type="button"
                                onClick={() => void remove(supplier)}
                              >
                                Удалить запись
                              </button>
                            </div>
                          )}
                        </div>
                      </td>
                    )}
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
                <dt>Кто подтвердил</dt>
                <dd>{selected.verified_by_name ?? "Не подтверждён пользователем"}</dd>
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
              <section className="registry-purchase-history">
                <h3>История итогов закупки</h3>
                {purchaseHistoryLoading && <p className="note">Загрузка…</p>}
                {!purchaseHistoryLoading && purchaseHistory.length === 0 && (
                  <p className="note">Сохранённых итогов с этим поставщиком нет.</p>
                )}
                {purchaseHistory.map((entry) => (
                  <article key={entry.id}>
                    <div>
                      <strong>Запрос #{entry.rfq_id}</strong>
                      <time dateTime={entry.created_at}>
                        {new Date(entry.created_at).toLocaleString("ru-RU")}
                      </time>
                    </div>
                    <p>{entry.note ?? "Комментарий не указан."}</p>
                    <span className="note">{entry.actor_name ?? "Сотрудник"}</span>
                  </article>
                ))}
              </section>
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
