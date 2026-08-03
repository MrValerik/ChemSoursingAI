// Раздел «Посредники»: реестр площадок, каталогов и перекупщиков.
//
// Зачем он нужен. Запрос по CAS-номеру поднимает в выдаче торговые площадки,
// а не заводы: маркетплейсы под эти номера оптимизируются, производители нет.
// Замер на стенде: из 74 найденных ссылок до оценки доходили пять, и все пять
// оказались перекупщиками. Поэтому в режиме поиска изготовителей эти домены
// отсеиваются до загрузки страниц, а бюджет уходит на сайты самих компаний.
//
// Список — данные, а не константа в коде: закупщик пополняет его сам. Он же
// пригодится, когда нужно наоборот сравнить цены среди всех продавцов.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, userErrorMessage } from "../api/client";
import type { IntermediaryKind, IntermediaryRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { IconButton, Input, Select } from "./ui";

const KIND_LABELS: Record<IntermediaryKind, string> = {
  marketplace: "Торговая площадка",
  catalog: "Каталог веществ",
  reseller: "Перекупщик",
  reference: "Справочный сайт",
};

const KIND_ORDER = Object.keys(KIND_LABELS) as IntermediaryKind[];

const KIND_HINTS: Record<IntermediaryKind, string> = {
  marketplace: "Витрина с объявлениями многих продавцов",
  catalog: "Справочник по веществам со списком поставщиков",
  reseller: "Конкретная компания, которая перепродаёт чужой товар",
  reference: "Сайт не о торговле: энциклопедия, соцсеть, база данных",
};

export default function IntermediariesSection() {
  const { user } = useAuth();
  const canEdit = user?.role === "head" || user?.role === "admin";

  const [items, setItems] = useState<IntermediaryRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [domain, setDomain] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<IntermediaryKind>("marketplace");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api.listIntermediaries());
      setError(null);
    } catch (e) {
      setError(userErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Одна таблица вместо четырёх: вид стал колонкой, а порядок сохраняет
  // прежнюю группировку — записи одного вида по-прежнему идут подряд.
  const ordered = useMemo(
    () =>
      [...items].sort(
        (left, right) =>
          KIND_ORDER.indexOf(left.kind) - KIND_ORDER.indexOf(right.kind) ||
          left.domain.localeCompare(right.domain, "ru"),
      ),
    [items],
  );

  const activeCount = items.filter((item) => item.is_active).length;

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!domain.trim() || !name.trim()) return;
    setSaving(true);
    try {
      await api.createIntermediary({ domain, name, kind });
      setDomain("");
      setName("");
      await load();
      setError(null);
    } catch (e) {
      setError(userErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (item: IntermediaryRead) => {
    try {
      await api.updateIntermediary(item.id, { is_active: !item.is_active });
      await load();
    } catch (e) {
      setError(userErrorMessage(e));
    }
  };

  const remove = async (item: IntermediaryRead) => {
    try {
      await api.deleteIntermediary(item.id);
      await load();
    } catch (e) {
      setError(userErrorMessage(e));
    }
  };

  return (
    <div className="requests-page intermediaries-page">
      <div className="requests-header">
        <div>
          <h1>Посредники</h1>
          <p className="note">
            Домены, которые не являются сайтами производителей. При поиске
            изготовителей эти ссылки откладываются до загрузки страниц, чтобы
            бюджет уходил на сайты самих компаний. В режиме «все продавцы»
            отсев не применяется — там площадка такой же источник цены.
          </p>
          <p className="note">
            Учитывается <strong>{activeCount}</strong> из {items.length}
          </p>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {canEdit && (
        <form className="requests-filters intermediary-form" onSubmit={add}>
          <Input
            className="field-domain"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="домен, например echemi.com"
            aria-label="Домен посредника"
          />
          <Input
            className="field-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="название"
            aria-label="Название посредника"
          />
          <Select
            value={kind}
            onChange={(next) => setKind(next as IntermediaryKind)}
            ariaLabel="Вид посредника"
            title={KIND_HINTS[kind]}
            options={KIND_ORDER.map((value) => ({
              value,
              label: KIND_LABELS[value],
            }))}
          />
          <button type="submit" disabled={saving}>
            {saving ? "Сохраняю…" : "Добавить"}
          </button>
        </form>
      )}

      {loading && <p className="note">Загрузка…</p>}

      {!loading && items.length === 0 && (
        <div className="panel">
          <p className="note">
            Реестр пуст. Добавьте домен площадки или каталога, чтобы поиск
            изготовителей перестал тратить бюджет на его страницы.
          </p>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="panel table-panel data-table intermediaries-list">
          <table className="summary requests-table">
            <thead>
              <tr>
                <th>Домен</th>
                <th>Название</th>
                <th>Вид</th>
                <th>Учитывается</th>
                {canEdit && <th className="request-actions-column" />}
              </tr>
            </thead>
            <tbody>
              {ordered.map((item) => (
                <tr key={item.id} className={item.is_active ? "" : "row-muted"}>
                  <td>
                    <strong>{item.domain}</strong>
                  </td>
                  <td>{item.name}</td>
                  <td title={KIND_HINTS[item.kind]}>{KIND_LABELS[item.kind]}</td>
                  <td>
                    <span
                      className={`badge ${item.is_active ? "tone-ok" : "tone-neutral"}`}
                    >
                      {item.is_active ? "да" : "нет"}
                    </span>
                  </td>
                  {canEdit && (
                    <td className="request-actions-column">
                      <div className="row-actions">
                        <button
                          type="button"
                          className="secondary btn-small"
                          onClick={() => void toggle(item)}
                        >
                          {item.is_active ? "Отключить" : "Включить"}
                        </button>
                        <IconButton
                          icon="trash"
                          label={`Удалить ${item.domain}`}
                          onClick={() => void remove(item)}
                        />
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
