import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type { SubstanceRecord } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Field, Icon, Input, Textarea } from "./ui";

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

const splitNames = (value: string) =>
  value
    .split(/[\n,;]/)
    .map((item) => item.trim())
    .filter(Boolean);

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
  const [synonyms, setSynonyms] = useState("");
  const [excludedNames, setExcludedNames] = useState("");
  const [notes, setNotes] = useState("");

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
    setSynonyms(selected.synonyms.join("\n"));
    setExcludedNames(selected.excluded_names.join("\n"));
    setNotes(selected.notes ?? "");
  }, [selected, creating]);

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
    setSynonyms("");
    setExcludedNames("");
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
            synonyms: splitNames(synonyms),
            excluded_names: splitNames(excludedNames),
            notes: notes.trim() || null,
          })
        : await api.updateSubstance(selected!.id, {
            preferred_name: preferredName.trim(),
            synonyms: splitNames(synonyms),
            excluded_names: splitNames(excludedNames),
            notes: notes.trim() || null,
          });
      setCreating(false);
      setSelectedId(saved.id);
      setNotice("Правила идентификации сохранены и будут применяться в новых поисках.");
      await load();
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
              <Field
                label="Допустимые синонимы"
                hint="По одному названию в строке. ИИ использует их как эквивалентные варианты."
              >
                <Textarea
                  disabled={!canEdit}
                  rows={6}
                  value={synonyms}
                  onChange={(event) => setSynonyms(event.target.value)}
                />
              </Field>
              <Field
                label="Исключённые названия"
                hint="Эти варианты не будут считаться эквивалентными веществу."
              >
                <Textarea
                  disabled={!canEdit}
                  rows={4}
                  value={excludedNames}
                  onChange={(event) => setExcludedNames(event.target.value)}
                />
              </Field>
              <Field label="Комментарий специалиста">
                <Textarea
                  disabled={!canEdit}
                  rows={3}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </Field>
              {error && <p className="error">Ошибка: {error}</p>}
              {notice && <p className="success">{notice}</p>}
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
