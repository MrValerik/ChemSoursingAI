import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  PurchaseHistoryEntry,
  SubstanceHistoryEntry,
  SubstanceLinkedRequest,
  SubstancePriceHistoryItem,
  SubstanceRecord,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Field, HelpTip, Icon, Input, Textarea, Toast } from "./ui";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";

const REVIEW_LABELS: Record<string, string> = {
  confirmed: "Подтверждено специалистом",
  needs_review: "Требует уточнения",
  unreviewed: "Не проверено специалистом",
};

const REVIEW_TONES: Record<string, string> = {
  confirmed: "tone-ok",
  needs_review: "tone-warn",
  unreviewed: "tone-neutral",
};

const HISTORY_LABELS: Record<string, string> = {
  created_from_request: "Карточка создана из закупочного запроса",
  catalog_confirmed: "Автоматическая карточка подтверждена специалистом",
  created: "Карточка создана и подтверждена",
  rules_updated: "Экспертные правила обновлены",
  identity_confirmed: "Идентификация ИИ подтверждена",
  identity_rejected: "Предложение ИИ отклонено",
  cas_corrected: "CAS-номер исправлен",
};

const CHANGE_LABELS: Record<string, string> = {
  cas: "CAS-номер",
  preferred_name: "Предпочтительное наименование",
  synonyms: "Допустимые синонимы",
  excluded_names: "Исключённые названия",
  notes: "Комментарий специалиста",
  review_status: "Статус проверки",
};

const formatHistoryValue = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "не задано";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "нет";
  if (value === "confirmed") return "подтверждено специалистом";
  if (value === "needs_review") return "требует уточнения";
  if (value === "unreviewed") return "не проверено";
  return String(value);
};

function TagEditor({
  label,
  hint,
  placeholder,
  values,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  placeholder: string;
  values: string[];
  disabled: boolean;
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const value = draft.trim();
    if (!value) return;
    if (!values.some((item) => item.toLocaleLowerCase() === value.toLocaleLowerCase())) {
      onChange([...values, value]);
    }
    setDraft("");
  };

  return (
    <div className="ui-field substance-tag-field">
      <span className="ui-field-label">{label}</span>
      <div className="tag-editor-control">
        <Input
          disabled={disabled}
          placeholder={placeholder}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
        <button
          className="secondary"
          disabled={disabled || !draft.trim()}
          type="button"
          onClick={add}
        >
          Добавить
        </button>
      </div>
      {values.length > 0 && (
        <div className="substance-name-tags">
          {values.map((value) => (
            <span className="substance-name-tag" key={value.toLocaleLowerCase()}>
              {value}
              {!disabled && (
                <button
                  aria-label={`Удалить ${value}`}
                  type="button"
                  onClick={() => onChange(values.filter((item) => item !== value))}
                >
                  <Icon name="close" size={12} />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      <span className="ui-field-hint">{hint}</span>
    </div>
  );
}

export default function SubstancesSection() {
  // Открытая карточка вещества — часть адреса: /substances/17.
  const { substanceId } = useParams();
  const navigate = useNavigate();
  const focusId = substanceId ? Number(substanceId) : null;
  const { user } = useAuth();
  const canEdit = user?.role !== "auditor";
  const [items, setItems] = useState<SubstanceRecord[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(focusId ?? null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [preferredName, setPreferredName] = useState("");
  const [cas, setCas] = useState("");
  const [synonyms, setSynonyms] = useState<string[]>([]);
  const [excludedNames, setExcludedNames] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [history, setHistory] = useState<SubstanceHistoryEntry[]>([]);
  const [purchaseHistory, setPurchaseHistory] = useState<PurchaseHistoryEntry[]>([]);
  const [linkedRequests, setLinkedRequests] = useState<SubstanceLinkedRequest[]>([]);
  const [priceHistory, setPriceHistory] = useState<SubstancePriceHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  // История открывается по требованию: в карточке с десятками решений она
  // отодвигает вниз и правила, и кнопку сохранения.
  const [historyOpen, setHistoryOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.listSubstances();
      setItems(data);
      setError(null);
      setSelectedId((current) => current ?? data[0]?.id ?? null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (focusId != null) {
      setSelectedId(focusId);
      setCreating(false);
    }
  }, [focusId]);

  const selected = items.find((item) => item.id === selectedId) ?? null;

  useEffect(() => {
    if (!selected || creating) return;
    setPreferredName(selected.preferred_name);
    setCas(selected.cas);
    setSynonyms(
      selected.synonyms.filter(
        (name) => name.toLocaleLowerCase() !== selected.preferred_name.toLocaleLowerCase(),
      ),
    );
    setExcludedNames(selected.excluded_names);
    setNotes(selected.notes ?? "");
  }, [selected, creating]);

  useEffect(() => {
    setHistoryOpen(false);
    if (selectedId === null || creating) {
      setHistory([]);
      setPurchaseHistory([]);
      setLinkedRequests([]);
      setPriceHistory([]);
      setHistoryError(null);
      return;
    }
    let active = true;
    setHistoryLoading(true);
    Promise.all([
      api.listSubstanceHistory(selectedId),
      api.listSubstancePurchaseHistory(selectedId),
      api.listSubstanceRequests(selectedId),
      api.listSubstancePriceHistory(selectedId),
    ])
      .then(([data, purchases, requests, prices]) => {
        if (!active) return;
        setHistory(data);
        setPurchaseHistory(purchases);
        setLinkedRequests(requests);
        setPriceHistory(prices);
        setHistoryError(null);
      })
      .catch((caught) => {
        if (!active) return;
        setHistoryError(caught instanceof ApiError ? caught.message : String(caught));
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedId, creating]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru");
    if (!needle) return items;
    return items.filter(
      (item) =>
        item.cas.toLowerCase().includes(needle) ||
        item.preferred_name.toLowerCase().includes(needle) ||
        item.synonyms.some((name) => name.toLowerCase().includes(needle)),
    );
  }, [items, search]);

  const beginCreate = () => {
    setCreating(true);
    setSelectedId(null);
    setPreferredName("");
    setCas("");
    setSynonyms([]);
    setExcludedNames([]);
    setNotes("");
    setError(null);
    setNotice(null);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = creating
        ? await api.createSubstance({
            cas: cas.trim(),
            preferred_name: preferredName.trim(),
            synonyms,
            excluded_names: excludedNames,
            notes: notes.trim() || null,
          })
        : await api.updateSubstance(selected!.id, {
            cas: cas.trim(),
            preferred_name: preferredName.trim(),
            synonyms,
            excluded_names: excludedNames,
            notes: notes.trim() || null,
          });
      setCreating(false);
      setSelectedId(saved.id);
      setNotice("Правила идентификации сохранены и будут применяться в новых поисках.");
      const [
        ,
        updatedHistory,
        updatedPurchaseHistory,
        updatedRequests,
        updatedPrices,
      ] = await Promise.all([
        load(),
        api.listSubstanceHistory(saved.id),
        api.listSubstancePurchaseHistory(saved.id),
        api.listSubstanceRequests(saved.id),
        api.listSubstancePriceHistory(saved.id),
      ]);
      setHistory(updatedHistory);
      setPurchaseHistory(updatedPurchaseHistory);
      setLinkedRequests(updatedRequests);
      setPriceHistory(updatedPrices);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="requests-page substances-page">
      <div className="requests-header">
        <div>
          <h1>Химические вещества</h1>
          <p className="note">
            Единый справочник названий и экспертных правил для всех закупочных запросов.
          </p>
        </div>
        {canEdit && (
          <button className="secondary button-with-icon" onClick={beginCreate}>
            <Icon name="flask" size={17} />
            Добавить вещество
          </button>
        )}
      </div>

      <div className="substance-catalog-layout">
        <section className="panel substance-list-panel">
          <div className="input-with-icon">
            <Icon name="search" size={17} />
            <Input
              aria-label="Поиск по справочнику веществ"
              placeholder="Название, синоним или CAS"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          {loading && <p className="note">Загрузка справочника…</p>}
          {!loading && filtered.length === 0 && (
            <p className="note">Подходящих веществ не найдено.</p>
          )}
          <div className="substance-list">
            {filtered.map((item) => (
              <button
                className={`substance-list-item ${selectedId === item.id ? "active" : ""}`}
                key={item.id}
                onClick={() => {
                  setSelectedId(item.id);
                  setCreating(false);
                  setNotice(null);
                  // Отказ по одной карточке не должен висеть над другой:
                  // «CAS занят» относится к номеру, который здесь уже не тот.
                  setError(null);
                }}
              >
                <span>
                  <strong>{item.preferred_name}</strong>
                  <small>CAS {item.cas}</small>
                </span>
                <span className={`badge ${REVIEW_TONES[item.review_status] ?? "tone-neutral"}`}>
                  {REVIEW_LABELS[item.review_status] ?? item.review_status}
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="panel substance-editor">
          {!selected && !creating ? (
            <div className="substance-empty">
              <Icon name="flask" size={28} />
              <strong>Выберите вещество</strong>
              <span className="note">
                Здесь можно проверить и изменить названия, которые используют ИИ-агенты.
              </span>
            </div>
          ) : (
            <>
              <div className="substance-editor-header">
                <div>
                  <h2>{creating ? "Новое вещество" : selected?.preferred_name}</h2>
                  {!creating && selected && (
                    <p className="note">
                      Использовано в запросах: {selected.request_count}
                      {selected.reviewed_by_name
                        ? ` · последнее решение: ${selected.reviewed_by_name}`
                        : ""}
                    </p>
                  )}
                </div>
                {!creating && selected && (
                  <span className={`badge ${REVIEW_TONES[selected.review_status] ?? "tone-neutral"}`}>
                    {REVIEW_LABELS[selected.review_status] ?? selected.review_status}
                  </span>
                )}
              </div>

              <div className="row">
                <Field label="Предпочтительное наименование">
                  <Input
                    disabled={!canEdit}
                    value={preferredName}
                    onChange={(event) => setPreferredName(event.target.value)}
                  />
                </Field>
                <Field
                  hint={
                    creating
                      ? undefined
                      : "Исправьте, если в карточку попал не тот номер: он приходит в справочник из первого запроса вместе с опечаткой закупщика или прайса. Номер проверяется контрольной суммой, занятый другой карточкой не принимается, а прежнее значение остаётся в истории изменений. Связанные запросы сохраняют номер, с которым были созданы и отправлены поставщикам."
                  }
                  label="CAS-номер"
                >
                  <Input
                    disabled={!canEdit}
                    value={cas}
                    onChange={(event) => setCas(event.target.value)}
                  />
                </Field>
              </div>
              <TagEditor
                disabled={!canEdit}
                hint="ИИ-агенты используют эти названия как допустимые варианты того же вещества."
                label="Допустимые синонимы"
                placeholder="Например, Acetylsalicylic acid"
                values={synonyms}
                onChange={setSynonyms}
              />
              <TagEditor
                disabled={!canEdit}
                hint="ИИ-агенты исключают эти названия из вариантов идентификации и поиска."
                label="Исключённые названия"
                placeholder="Название другого вещества или ошибочный вариант"
                values={excludedNames}
                onChange={setExcludedNames}
              />
              <Field
                label={
                  <span className="field-label-with-help">
                    Комментарий специалиста
                    <HelpTip text="Укажите особенности грейда, назначения, состава, неоднозначные торговые названия или обязательные ограничения. Комментарий передаётся ИИ-агентам при идентификации, планировании поиска и проверке поставщиков для будущих запросов по этой карточке." />
                  </span>
                }
                hint="Это постоянное экспертное правило для последующих поисков по веществу."
              >
                <Textarea
                  disabled={!canEdit}
                  rows={3}
                  placeholder="Например: искать только фармацевтический USP-грейд; не считать технический продукт эквивалентом"
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </Field>
              {!creating && selected && (
                <section className="substance-related-section">
                  <div className="heading-with-help">
                    <h3>Связанные запросы</h3>
                    <HelpTip text="Здесь показаны закупочные запросы, автоматически связанные с карточкой по точному CAS-номеру. Доступ зависит от вашей роли и ответственного за запрос." />
                  </div>
                  {historyLoading && <p className="note">Загрузка запросов…</p>}
                  {!historyLoading && linkedRequests.length === 0 && (
                    <p className="note">Связанных запросов пока нет.</p>
                  )}
                  {linkedRequests.length > 0 && (
                    <div className="table-scroll">
                      <table className="summary substance-related-table">
                        <thead>
                          <tr>
                            <th>Запрос</th>
                            <th>Дата</th>
                            <th>Объём</th>
                            <th>Статус</th>
                            <th>Котировки</th>
                            <th>Ответственный</th>
                          </tr>
                        </thead>
                        <tbody>
                          {linkedRequests.map((request) => (
                            <tr key={request.id}>
                              <td>
                                <button
                                  className="link-btn"
                                  type="button"
                                  onClick={() => navigate(`/requests/${request.id}`)}
                                >
                                  #{request.id} · {request.name}
                                </button>
                              </td>
                              <td>
                                {new Date(request.created_at).toLocaleDateString("ru-RU")}
                              </td>
                              <td>{request.volume ?? "—"}</td>
                              <td>
                                <span className={`badge tone-${STATUS_TONE[request.status]}`}>
                                  {STATUS_LABELS[request.status]}
                                </span>
                              </td>
                              <td>{request.quotation_count}</td>
                              <td>{request.owner_name ?? "Не назначен"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              )}
              {!creating && selected && (
                <section className="substance-related-section">
                  <div className="heading-with-help">
                    <h3>История цен</h3>
                    <HelpTip text="Каждая строка — сохранённая котировка по связанному запросу. Цена показывается вместе с валютой, единицей и базисом; несопоставимые условия не пересчитываются автоматически." />
                  </div>
                  {historyLoading && <p className="note">Загрузка цен…</p>}
                  {!historyLoading && priceHistory.length === 0 && (
                    <p className="note">Цены по этому веществу ещё не получены.</p>
                  )}
                  {priceHistory.length > 0 && (
                    <div className="table-scroll">
                      <table className="summary substance-related-table">
                        <thead>
                          <tr>
                            <th>Дата</th>
                            <th>Цена</th>
                            <th>Базис</th>
                            <th>Количество / MOQ</th>
                            <th>Поставщик</th>
                            <th>Запрос</th>
                          </tr>
                        </thead>
                        <tbody>
                          {priceHistory.map((entry) => (
                            <tr key={entry.quotation_id}>
                              <td>
                                {new Date(entry.quoted_at).toLocaleDateString("ru-RU")}
                              </td>
                              <td>
                                {entry.price.toLocaleString("ru-RU")} {entry.currency ?? ""}
                                {entry.price_unit ? ` / ${entry.price_unit}` : ""}
                              </td>
                              <td>{entry.incoterm ?? "—"}</td>
                              <td>{entry.quoted_quantity ?? entry.moq ?? "—"}</td>
                              <td>{entry.supplier_name ?? "Не указан"}</td>
                              <td>
                                <button
                                  className="link-btn"
                                  type="button"
                                  onClick={() => navigate(`/requests/${entry.rfq_id}/summary`)}
                                >
                                  #{entry.rfq_id}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              )}
              {!creating && selected && (
                <section className="substance-history">
                  <div className="tab-toolbar">
                    <div className="heading-with-help">
                      <h3>История изменений</h3>
                      <HelpTip text="История показывает каждое экспертное подтверждение и изменение правил: кто принял решение, когда и какие значения поменялись. Исправленный CAS-номер тоже остаётся здесь вместе с прежним значением." />
                    </div>
                    <div className="substance-history-toggle">
                      {!historyLoading && !historyError && (
                        <span className="badge tone-neutral">
                          записей: {history.length + purchaseHistory.length}
                        </span>
                      )}
                      <button
                        aria-expanded={historyOpen}
                        className="secondary btn-small"
                        type="button"
                        onClick={() => setHistoryOpen((open) => !open)}
                      >
                        {historyOpen ? "Свернуть" : "Развернуть"}
                      </button>
                    </div>
                  </div>
                  {historyOpen && historyLoading && (
                    <p className="note">Загрузка истории…</p>
                  )}
                  {historyOpen && historyError && (
                    <p className="error">Не удалось загрузить историю: {historyError}</p>
                  )}
                  {historyOpen &&
                    !historyLoading &&
                    !historyError &&
                    history.length === 0 &&
                    purchaseHistory.length === 0 && (
                    <p className="note">
                      История начнёт формироваться после следующего экспертного решения.
                    </p>
                  )}
                  {historyOpen && (
                    <div className="substance-history-list">
                      {history.map((entry) => (
                        <article className="substance-history-entry" key={entry.id}>
                          <div className="substance-history-entry-header">
                            <strong>{HISTORY_LABELS[entry.action] ?? entry.action}</strong>
                            <time dateTime={entry.created_at}>
                              {new Date(entry.created_at).toLocaleString("ru-RU", {
                                dateStyle: "medium",
                                timeStyle: "short",
                              })}
                            </time>
                          </div>
                          <div className="substance-history-meta">
                            <span>
                              Подтвердил: {entry.actor_name ?? `пользователь #${entry.actor_id}`}
                            </span>
                            {entry.source_rfq_id !== null && (
                              <span>Основание: запрос #{entry.source_rfq_id}</span>
                            )}
                          </div>
                          {Object.keys(entry.changes).length > 0 && (
                            <ul>
                              {Object.entries(entry.changes).map(([field, change]) => (
                                <li key={field}>
                                  <span>{CHANGE_LABELS[field] ?? field}</span>
                                  <strong>
                                    {formatHistoryValue(change.before)} →{" "}
                                    {formatHistoryValue(change.after)}
                                  </strong>
                                </li>
                              ))}
                            </ul>
                          )}
                        </article>
                      ))}
                      {purchaseHistory.map((entry) => (
                        <article className="substance-history-entry" key={`purchase-${entry.id}`}>
                          <div className="substance-history-entry-header">
                            <strong>Итог закупки сохранён</strong>
                            <time dateTime={entry.created_at}>
                              {new Date(entry.created_at).toLocaleString("ru-RU", {
                                dateStyle: "medium",
                                timeStyle: "short",
                              })}
                            </time>
                          </div>
                          <div className="substance-history-meta">
                            <span>{entry.actor_name ?? "Сотрудник"}</span>
                            <span>Запрос #{entry.rfq_id}</span>
                            {typeof entry.snapshot.supplier_name === "string" && (
                              <span>{entry.snapshot.supplier_name}</span>
                            )}
                          </div>
                          <p>{entry.note ?? "Комментарий не указан."}</p>
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              )}
              {error && <p className="error">Ошибка: {error}</p>}
              {notice && (
                <Toast message={notice} onClose={() => setNotice(null)} />
              )}
              {canEdit && (
                <div className="actions">
                  <button
                    className="button-with-icon"
                    disabled={busy || !preferredName.trim() || !cas.trim()}
                    onClick={() => void save()}
                  >
                    <Icon name="save" size={17} />
                    {busy ? "Сохранение…" : "Сохранить правила"}
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
