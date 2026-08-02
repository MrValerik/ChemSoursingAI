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

const KIND_LABELS: Record<IntermediaryKind, string> = {
  marketplace: "Торговая площадка",
  catalog: "Каталог веществ",
  reseller: "Перекупщик",
  reference: "Справочный сайт",
};

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

  const grouped = useMemo(() => {
    const byKind = new Map<IntermediaryKind, IntermediaryRead[]>();
    for (const item of items) {
      const list = byKind.get(item.kind) ?? [];
      list.push(item);
      byKind.set(item.kind, list);
    }
    return byKind;
  }, [items]);

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
    <section className="section">
      <header className="section-header">
        <h1>Посредники</h1>
        <p className="muted">
          Домены, которые не являются сайтами производителей. При поиске
          изготовителей эти ссылки откладываются до загрузки страниц, чтобы
          бюджет уходил на сайты самих компаний. В режиме «все продавцы»
          отсев не применяется — там площадка такой же источник цены.
        </p>
        <p className="muted">
          Действующих записей: <strong>{activeCount}</strong> из {items.length}
        </p>
      </header>

      {error && <div className="alert alert-error">{error}</div>}

      {canEdit && (
        <form className="inline-form" onSubmit={add}>
          <input
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="домен, например echemi.com"
            aria-label="Домен посредника"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="название"
            aria-label="Название посредника"
          />
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as IntermediaryKind)}
            aria-label="Вид посредника"
            title={KIND_HINTS[kind]}
          >
            {(Object.keys(KIND_LABELS) as IntermediaryKind[]).map((value) => (
              <option key={value} value={value}>
                {KIND_LABELS[value]}
              </option>
            ))}
          </select>
          <button type="submit" disabled={saving}>
            {saving ? "Сохраняю…" : "Добавить"}
          </button>
        </form>
      )}

      {loading ? (
        <p className="muted">Загрузка…</p>
      ) : (
        (Object.keys(KIND_LABELS) as IntermediaryKind[]).map((value) => {
          const list = grouped.get(value) ?? [];
          if (!list.length) return null;
          return (
            <div key={value} className="card">
              <h2 title={KIND_HINTS[value]}>{KIND_LABELS[value]}</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th>Домен</th>
                    <th>Название</th>
                    <th>Учитывается</th>
                    {canEdit && <th aria-label="Действия" />}
                  </tr>
                </thead>
                <tbody>
                  {list.map((item) => (
                    <tr key={item.id} className={item.is_active ? "" : "muted"}>
                      <td>{item.domain}</td>
                      <td>{item.name}</td>
                      <td>{item.is_active ? "да" : "нет"}</td>
                      {canEdit && (
                        <td>
                          <button type="button" onClick={() => void toggle(item)}>
                            {item.is_active ? "Отключить" : "Включить"}
                          </button>
                          <button type="button" onClick={() => void remove(item)}>
                            Удалить
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })
      )}
    </section>
  );
}
