import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type { SubstanceHistoryEntry, SubstanceRecord } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Field, HelpTip, Icon, Input, Textarea, Toast } from "./ui";

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
  created: "Карточка создана и подтверждена",
  rules_updated: "Экспертные правила обновлены",
  identity_confirmed: "Идентификация ИИ подтверждена",
  identity_rejected: "Предложение ИИ отклонено",
};

const CHANGE_LABELS: Record<string, string> = {
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

export default function SubstancesSection({
  focusId,
  onFocusConsumed,
}: {
  focusId?: number | null;
  onFocusConsumed?: () => void;
}) {
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
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

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
      onFocusConsumed?.();
    }
  }, [focusId, onFocusConsumed]);

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
    if (selectedId === null || creating) {
      setHistory([]);
      setHistoryError(null);
      return;
    }
    let active = true;
    setHistoryLoading(true);
    api
      .listSubstanceHistory(selectedId)
      .then((data) => {
        if (!active) return;
        setHistory(data);
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
            preferred_name: preferredName.trim(),
            synonyms,
            excluded_names: excludedNames,
            notes: notes.trim() || null,
          });
      setCreating(false);
      setSelectedId(saved.id);
      setNotice("Правила идентификации сохранены и будут применяться в новых поисках.");
      const [, updatedHistory] = await Promise.all([
        load(),
        api.listSubstanceHistory(saved.id),
      ]);
      setHistory(updatedHistory);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="substances-page">
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
                <Field label="CAS-номер">
                  <Input
                    disabled={!creating || !canEdit}
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
                <section className="substance-history">
                  <div className="heading-with-help">
                    <h3>История изменений</h3>
                    <HelpTip text="История показывает каждое экспертное подтверждение и изменение правил: кто принял решение, когда и какие значения поменялись." />
                  </div>
                  {historyLoading && <p className="note">Загрузка истории…</p>}
                  {historyError && (
                    <p className="error">Не удалось загрузить историю: {historyError}</p>
                  )}
                  {!historyLoading && !historyError && history.length === 0 && (
                    <p className="note">
                      История начнёт формироваться после следующего экспертного решения.
                    </p>
                  )}
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
                  </div>
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
                    disabled={
                      busy ||
                      !preferredName.trim() ||
                      (creating && !cas.trim())
                    }
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
