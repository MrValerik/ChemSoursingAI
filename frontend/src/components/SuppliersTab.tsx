// Вкладка «Поставщики» (раздел 9 UI/UX-плана): кандидаты из реестра,
// чекбоксы получателей, ручное добавление, переход к рассылке.
// Веб-сорсинг открытых источников появится на этапе интеграций.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ChannelKind, RecipientRead, SupplierRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { HelpTip } from "./ui";

const TYPE_LABELS: Record<string, string> = {
  manufacturer: "производитель",
  distributor: "дистрибьютор",
};

// Колонки, по которым таблицу имеет смысл упорядочивать. Источник сюда не
// входит: он ушёл в подробную карточку.
type SortKey = "company" | "type" | "channels" | "status" | "reputation";

const SORT_LABELS: Record<SortKey, string> = {
  company: "Компания",
  type: "Тип",
  channels: "Канал",
  status: "Статус отправки",
  reputation: "Репутация",
};

// Почему у компании нет канала. «Нет контакта» закупщик читает как
// «недостижима» и вычёркивает, хотя написать нередко можно — просто не
// автоматической рассылкой.
const BARRIER_LABELS: Record<string, string> = {
  obfuscated: "контакт зашифрован",
  form: "только форма на сайте",
};

const BARRIER_HELP: Record<string, string> = {
  obfuscated:
    "Адрес почты на странице компании есть, но сайт подменяет его заглушкой, " +
    "чтобы его не собирали спам-боты. В браузере адрес виден, а нашему " +
    "чтению страницы — нет. Откройте источник из карточки и посмотрите " +
    "адрес глазами: писать по нему можно, в автоматическую рассылку он не " +
    "попадёт.",
  form: "Ни почты, ни телефона компания не публикует — связь только через " +
    "форму обратной связи на её сайте. Заполнить и отправить форму может " +
    "только человек: откройте источник из карточки и напишите оттуда.",
};

function reputationValue(value: string | null): number {
  const n = Number(value);
  return Number.isNaN(n) ? -1 : n;
}

function Stars({ value }: { value: string | null }) {
  const n = Number(value);
  if (!value || Number.isNaN(n)) return <span className="note">{value ?? "—"}</span>;
  return (
    <span className="stars" title={`Репутация: ${n} из 5`}>
      {"★".repeat(Math.max(0, Math.min(5, Math.round(n))))}
      <span className="stars-empty">{"★".repeat(Math.max(0, 5 - Math.round(n)))}</span>
    </span>
  );
}

export default function SuppliersTab({
  rfqId,
  onGoToDispatch,
}: {
  rfqId: number;
  onGoToDispatch: () => void;
}) {
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";

  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [recipients, setRecipients] = useState<RecipientRead[]>([]);
  const [checked, setChecked] = useState<Map<number, ChannelKind>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [sortKey, setSortKey] = useState<SortKey>("company");
  const [sortAsc, setSortAsc] = useState(true);
  // Компания, раскрытая в подробной карточке.
  const [detailId, setDetailId] = useState<number | null>(null);

  const [addOpen, setAddOpen] = useState(false);
  const [newCompany, setNewCompany] = useState("");
  const [newType, setNewType] = useState("manufacturer");
  const [newEmail, setNewEmail] = useState("");
  const [newWhatsapp, setNewWhatsapp] = useState("");

  const load = async () => {
    try {
      const [s, r] = await Promise.all([
        api.listSuppliers(),
        api.listRecipients(rfqId),
      ]);
      setSuppliers(s);
      setRecipients(r);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId]);

  // Таблица показывает контрагентов этого запроса, а не весь справочник.
  // Реестр общий и растёт от всех прогонов подряд: на стенде в нём 164
  // компании, тогда как по запросу «Ацетилсалициловая кислота» найдено
  // пять. Показывая всё, вкладка предлагала написать про аспирин тем, кого
  // нашли по эпоксидированному соевому маслу.
  const forThisRequest = useMemo(
    () =>
      suppliers.filter((item) =>
        (item.linked_requests ?? []).some((link) => link.rfq_id === rfqId),
      ),
    [suppliers, rfqId],
  );

  const alreadySelected = useMemo(
    () => new Set(recipients.map((r) => r.supplier_id)),
    [recipients],
  );
  const recipientBySupplier = useMemo(
    () => new Map(recipients.map((item) => [item.supplier_id, item])),
    [recipients],
  );

  const sorted = useMemo(() => {
    const status = (id: number) => {
      const recipient = recipientBySupplier.get(id);
      return recipient?.note ?? recipient?.status ?? "";
    };
    const value = (s: SupplierRead): string | number => {
      switch (sortKey) {
        case "type":
          return s.type ? TYPE_LABELS[s.type] ?? s.type : "";
        case "channels":
          return s.channels.join(", ");
        case "status":
          return status(s.id);
        case "reputation":
          return reputationValue(s.reputation);
        default:
          return s.company;
      }
    };
    // Копия: sort меняет массив на месте, а исходный список приходит из
    // состояния и переупорядочивать его нельзя.
    return [...forThisRequest].sort((a, b) => {
      const left = value(a);
      const right = value(b);
      let diff: number;
      if (typeof left === "number" && typeof right === "number") {
        diff = left - right;
      } else {
        // localeCompare, а не сравнение строк: в реестре есть и кириллица,
        // и китайские названия, и при обычном сравнении они уезжают в конец.
        diff = String(left).localeCompare(String(right), "ru", {
          sensitivity: "base",
          numeric: true,
        });
      }
      // Равные значения упорядочиваем по названию, иначе строки прыгают
      // при каждом обновлении списка.
      if (diff === 0) diff = a.company.localeCompare(b.company, "ru");
      return sortAsc ? diff : -diff;
    });
  }, [forThisRequest, recipientBySupplier, sortKey, sortAsc]);

  const detail = useMemo(
    () => sorted.find((item) => item.id === detailId) ?? null,
    [sorted, detailId],
  );

  // Окно должно закрываться клавишей, а не только мышью: закупщик
  // просматривает таблицу подряд и не тянется к кнопке ради каждой строки.
  useEffect(() => {
    if (detailId === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailId]);

  const sortBy = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc((prev) => !prev);
      return;
    }
    setSortKey(key);
    setSortAsc(true);
  };

  const toggle = (s: SupplierRead) => {
    if (readOnly || alreadySelected.has(s.id) || s.channels.length === 0) return;
    setChecked((prev) => {
      const next = new Map(prev);
      if (next.has(s.id)) next.delete(s.id);
      else next.set(s.id, s.channels[0]);
      return next;
    });
  };

  const setChannel = (id: number, channel: ChannelKind) => {
    setChecked((prev) => new Map(prev).set(id, channel));
  };

  const submitSelection = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.selectRecipients(
        rfqId,
        [...checked.entries()].map(([supplier_id, channel]) => ({
          supplier_id,
          channel,
        })),
      );
      setChecked(new Map());
      await load();
      setNotice("Получатели добавлены. Перейдите в «Общение», проверьте RFQ и подтвердите отправку.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const addSupplier = async () => {
    setBusy(true);
    setError(null);
    try {
      // Номер запроса обязателен: без него поставщик заводится в общий
      // справочник, но с запросом не связывается — и в таблице этого
      // запроса не появляется вовсе.
      await api.addSupplier(
        {
          company: newCompany.trim(),
          type: newType,
          email: newEmail.trim() || null,
          whatsapp: newWhatsapp.trim() || null,
        },
        rfqId,
      );
      setAddOpen(false);
      setNewCompany("");
      setNewEmail("");
      setNewWhatsapp("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <div className="tab-toolbar">
        <h2>Поставщики для рассылки</h2>
        <div className="requests-actions">
          <button className="secondary" onClick={() => void load()}>
            Обновить поиск
          </button>
          {!readOnly && (
            <button className="secondary" onClick={() => setAddOpen((v) => !v)}>
              Добавить вручную
            </button>
          )}
        </div>
      </div>
      <p className="note">
        Контрагенты этого запроса: найденные поиском по открытым источникам и
        добавленные вручную. Роль и контакты взяты со страниц компаний и
        требуют подтверждения перепиской.
      </p>

      {addOpen && (
        <div className="add-supplier">
          <div className="row">
            <div className="field">
              <label>Компания *</label>
              <input value={newCompany} onChange={(e) => setNewCompany(e.target.value)} />
            </div>
            <div className="field">
              <label>Тип</label>
              <select value={newType} onChange={(e) => setNewType(e.target.value)}>
                <option value="manufacturer">производитель</option>
                <option value="distributor">дистрибьютор</option>
              </select>
            </div>
          </div>
          <div className="row">
            <div className="field">
              <label>Email</label>
              <input value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
            </div>
            <div className="field">
              <label>WhatsApp</label>
              <input value={newWhatsapp} onChange={(e) => setNewWhatsapp(e.target.value)} />
            </div>
          </div>
          <div className="actions">
            <button onClick={() => void addSupplier()} disabled={busy || !newCompany.trim()}>
              Сохранить
            </button>
            <button className="secondary" onClick={() => setAddOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {notice && <p className="success-note">{notice}</p>}

      <table className="summary">
        <thead>
          <tr>
            <th></th>
            {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
              <th key={key}>
                <button
                  type="button"
                  className="table-sort"
                  onClick={() => sortBy(key)}
                  aria-label={`Сортировать по «${SORT_LABELS[key]}»`}
                >
                  {SORT_LABELS[key]}
                  <span className="table-sort-mark">
                    {sortKey === key ? (sortAsc ? "▲" : "▼") : ""}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => {
            const selected = alreadySelected.has(s.id);
            const isChecked = checked.has(s.id);
            const recipient = recipientBySupplier.get(s.id);
            return (
              // Клик по строке раскрывает карточку, а не ставит галочку:
              // выбор получателя — действие с последствиями, и для него
              // остаётся сам чекбокс.
              <tr
                key={s.id}
                className={selected ? "row-muted" : "clickable"}
                onClick={() => setDetailId(s.id)}
              >
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={isChecked || selected}
                    disabled={readOnly || selected || s.channels.length === 0}
                    onChange={() => toggle(s)}
                    aria-label={`Выбрать «${s.company}» для рассылки`}
                  />
                </td>
                <td>
                  <div>{s.company}</div>
                  {s.certificates && s.certificates.length > 0 && (
                    <div className="cas">{s.certificates.join(", ")}</div>
                  )}
                </td>
                <td>{s.type ? TYPE_LABELS[s.type] : "—"}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  {s.channels.length === 0 &&
                    (s.contact_barrier ? (
                      <span className="note contact-barrier">
                        {BARRIER_LABELS[s.contact_barrier]}
                        <HelpTip text={BARRIER_HELP[s.contact_barrier]} />
                      </span>
                    ) : (
                      <span className="note">нет контакта</span>
                    ))}
                  {isChecked && s.channels.length > 1 ? (
                    <select
                      value={checked.get(s.id)}
                      onChange={(e) => setChannel(s.id, e.target.value as ChannelKind)}
                    >
                      {s.channels.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    s.channels.join(", ")
                  )}
                </td>
                <td>{recipient?.note ?? recipient?.status ?? "—"}</td>
                <td>
                  <Stars value={s.reputation} />
                </td>
              </tr>
            );
          })}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={6} className="note">
                По этому запросу контрагентов пока нет. Запустите поиск во
                вкладке «Поиск поставщиков» или добавьте компанию вручную.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {detail && (
        <div
          className="request-delete-backdrop"
          role="presentation"
          onClick={() => setDetailId(null)}
        >
          <section
            aria-labelledby="supplier-detail-title"
            aria-modal="true"
            className="supplier-detail"
            role="dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <h2 id="supplier-detail-title">{detail.company}</h2>
              <button
                className="secondary"
                type="button"
                onClick={() => setDetailId(null)}
              >
                Закрыть
              </button>
            </header>

            <dl className="supplier-detail-fields">
              <dt>Тип</dt>
              <dd>{detail.type ? TYPE_LABELS[detail.type] : "не определён"}</dd>

              <dt>Страна</dt>
              <dd>{detail.country ?? "—"}</dd>

              <dt>Источник</dt>
              <dd>
                {detail.source && detail.source.startsWith("http") ? (
                  <a href={detail.source} target="_blank" rel="noreferrer">
                    {detail.source}
                  </a>
                ) : (
                  detail.source ?? "—"
                )}
              </dd>

              <dt>Контакты</dt>
              <dd>
                {detail.contacts.length === 0 ? (
                  <span className="note">
                    {detail.contact_barrier ? (
                      <>
                        {BARRIER_LABELS[detail.contact_barrier]}
                        <HelpTip text={BARRIER_HELP[detail.contact_barrier]} />
                      </>
                    ) : (
                      "Связи нет: на странице компании не нашлось ни почты, ни телефона."
                    )}
                  </span>
                ) : (
                  <ul className="supplier-detail-contacts">
                    {detail.contacts.map((contact) => (
                      <li key={contact.id}>
                        {contact.full_name && <span>{contact.full_name}: </span>}
                        {contact.email && (
                          <a href={`mailto:${contact.email}`}>{contact.email}</a>
                        )}
                        {contact.email && contact.whatsapp && " · "}
                        {contact.whatsapp && <span>WhatsApp {contact.whatsapp}</span>}
                        {contact.offered_substances &&
                          contact.offered_substances.length > 0 && (
                            <div className="cas">
                              по запросам: {contact.offered_substances.join(", ")}
                            </div>
                          )}
                      </li>
                    ))}
                  </ul>
                )}
              </dd>

              <dt>Сертификаты</dt>
              <dd>
                {detail.certificates && detail.certificates.length > 0
                  ? detail.certificates.join(", ")
                  : "—"}
              </dd>

              <dt>Балл проверки</dt>
              <dd>
                {detail.evidence_score ?? "—"}
                {detail.evidence_score !== null && " из 100"}
              </dd>

              <dt>Статус в реестре</dt>
              <dd>{detail.qualification_status}</dd>

              <dt>Другие запросы</dt>
              <dd>
                {detail.linked_requests.length <= 1
                  ? "только этот"
                  : detail.linked_requests
                      .filter((link) => link.rfq_id !== rfqId)
                      .map((link) => link.name)
                      .join("; ")}
              </dd>
            </dl>

            <p className="note">
              Роль и контакты прочитаны со страницы компании и подтверждения не
              заменяют: точный ответ даст переписка.
            </p>
          </section>
        </div>
      )}

      <div className="tab-footer">
        <span className="note">
          Выбрано: {checked.size}
          {alreadySelected.size > 0 ? ` · уже в рассылке: ${alreadySelected.size}` : ""}
        </span>
        <div className="actions">
          <button
            className="secondary"
            onClick={() => void submitSelection()}
            disabled={busy || checked.size === 0}
          >
            Добавить получателей
          </button>
          <button onClick={onGoToDispatch} disabled={busy || recipients.length === 0}>
            Перейти к предпросмотру RFQ
          </button>
        </div>
      </div>
    </div>
  );
}
