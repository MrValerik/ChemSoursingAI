// Раздел «Запросы» (раздел 6 UI/UX-плана): сводная таблица всех RFQ
// с фильтрами, быстрыми чипами, сортировкой и экспортом CSV.
//
// Строка отвечает на три вопроса подряд: что за заявка, что с ней нужно
// сделать прямо сейчас и как идёт переписка. Статус RFQ для этого не
// годился — он описывает, что успела сделать система, а не что должен
// сделать человек, поэтому первым после названия идёт вычисленное
// сервером ближайшее действие, а не бейдж статуса.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  CommunicationOverviewRead,
  RFQListItem,
  RFQNextAction,
  RFQStage,
  SupplierConversationRead,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { STATUS_LABELS } from "./statusLabels";
import {
  ACTION_TONE,
  STAGE_LABELS,
  STAGE_ORDER,
  actionChip,
  actionUrgency,
  days,
} from "./requestProgress";
import { Icon, Input, MultiSelect, Toast } from "./ui";

type QuickFilter =
  | "all"
  | "todo"
  | "reply"
  | "silence"
  | "undispatched"
  | "decide";
type ScopeFilter = "mine" | "all";
type SortKey = "id" | "name" | "action" | "dispatched_at" | "owner_name";

const formatDate = (value: string) => new Date(value).toLocaleDateString("ru-RU");

const formatMoment = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

// «3 сент.» вместо «03.09.2026»: в колонке сроков важен порядок дней, а не
// точность до года, и короткая форма оставляет место второй строке.
const formatShortDate = (value: string) =>
  new Date(value).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

const daysSince = (value: string) => {
  const diff = Date.now() - new Date(value).getTime();
  return Math.max(0, Math.floor(diff / 86_400_000));
};

const agoLabel = (value: string) => {
  const passed = daysSince(value);
  if (passed === 0) return "сегодня";
  if (passed === 1) return "вчера";
  return `${days(passed)} назад`;
};

const CHANNEL_LABELS: Record<string, string> = {
  email: "почта",
  whatsapp: "WhatsApp",
};

// Состояние сбора данных по одной компании — те же слова, что во вкладке
// «Общение» карточки, чтобы раскрытая строка и карточка не спорили.
const COLLECTION_LABELS: Record<string, { label: string; tone: string }> = {
  complete: { label: "Данные собраны", tone: "tone-ok" },
  needs_human: { label: "Нужен человек", tone: "tone-warn" },
  collecting: { label: "Сбор данных", tone: "tone-info" },
  not_started: { label: "Ответа нет", tone: "tone-neutral" },
};

// Быстрые чипы отбирают строки по ближайшему действию, а не по статусу:
// «требуют внимания» раньше срабатывал почти на всём, что в работе, и
// потому ничего не выделял.
const QUICK_ACTIONS: Record<QuickFilter, RFQNextAction[] | null> = {
  all: null,
  todo: ["escalation", "dispatch_error", "reply", "silence"],
  reply: ["reply"],
  silence: ["silence"],
  undispatched: ["verify", "search", "dispatch"],
  decide: ["decide"],
};

const ALL_ACTIONS: RFQNextAction[] = [
  "escalation",
  "dispatch_error",
  "reply",
  "silence",
  "decide",
  "incomplete",
  "waiting",
  "dispatch",
  "verify",
  "search",
  "closed",
];

// Подписи действий в фильтре — без чисел, которые есть только у строки.
const ACTION_FILTER_LABELS: Record<RFQNextAction, string> = {
  escalation: "Разобрать вручную",
  dispatch_error: "Не ушло письмо",
  reply: "Ответить поставщику",
  silence: "Напомнить о себе",
  decide: "Сравнить предложения",
  incomplete: "Собираем данные",
  waiting: "Ждём ответов",
  dispatch: "Разослать запросы",
  verify: "Проверить вещество",
  search: "Найти поставщиков",
  closed: "Закрыт",
};

// Список перезапрашивается при монтировании: возврат из карточки — это
// переход по адресу, а не смена внутреннего состояния, поэтому счётчик
// обновления больше не нужен.
export default function RequestsTable({
  onOpen,
  onNew,
}: {
  onOpen: (id: number) => void;
  onNew: () => void;
}) {
  const { user } = useAuth();
  const showOwner = user?.role === "head" || user?.role === "admin" || user?.role === "auditor";

  const [rows, setRows] = useState<RFQListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<RFQListItem | null>(null);

  // Раскрытая строка догружает переписку по требованию: список остаётся
  // одним запросом, а «с кем именно идёт диалог» видно, не уходя из него.
  const [expanded, setExpanded] = useState<number | null>(null);
  const [dialogues, setDialogues] = useState<
    Record<number, SupplierConversationRead[]>
  >({});
  const [dialogueError, setDialogueError] = useState<Record<number, string>>({});
  const [dialogueLoading, setDialogueLoading] = useState<number | null>(null);

  const [quick, setQuick] = useState<QuickFilter>("all");
  const [scope, setScope] = useState<ScopeFilter>("mine");
  const [actionFilters, setActionFilters] = useState<string[]>([]);
  const [ownerFilters, setOwnerFilters] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  // По умолчанию наверху самое срочное: со списка начинают день, а не
  // смотрят в него, что завели последним.
  const [sortKey, setSortKey] = useState<SortKey>("action");
  const [sortAsc, setSortAsc] = useState(true);

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
  }, []);

  const owners = useMemo(
    () => [...new Set(rows.map((r) => r.owner_name).filter((o): o is string => !!o))].sort(),
    [rows],
  );

  const filtered = useMemo(() => {
    let out = rows;
    if (scope === "mine" && user) out = out.filter((r) => r.owner_id === user.id);
    const quickActions = QUICK_ACTIONS[quick];
    if (quickActions) out = out.filter((r) => quickActions.includes(r.next_action));
    if (actionFilters.length > 0) {
      out = out.filter((r) => actionFilters.includes(r.next_action));
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
      if (sortKey === "action") {
        const byUrgency = actionUrgency(a) - actionUrgency(b);
        // Внутри одной срочности первым идёт то, что ждёт дольше.
        const byWait = (b.waiting_days ?? -1) - (a.waiting_days ?? -1);
        return (byUrgency || byWait || b.id - a.id) * dir;
      }
      if (sortKey === "dispatched_at") {
        // Неразосланные заявки уходят в конец при любом направлении: даты
        // у них нет, и подмешивать их к самым свежим бессмысленно.
        if (!a.dispatched_at && !b.dispatched_at) return b.id - a.id;
        if (!a.dispatched_at) return 1;
        if (!b.dispatched_at) return -1;
        return (a.dispatched_at < b.dispatched_at ? -1 : 1) * dir;
      }
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv), "ru") * dir;
    });
  }, [rows, scope, user, quick, actionFilters, ownerFilters, search, sortKey, sortAsc]);

  const mineCount = user
    ? rows.filter((row) => row.owner_id === user.id).length
    : 0;

  // Счётчики на чипах: сколько строк попадёт под каждый отбор в текущем
  // охвате. Без них «Требуют действия» приходится нажимать, чтобы узнать,
  // есть ли там вообще что-нибудь.
  const scoped = useMemo(
    () =>
      scope === "mine" && user
        ? rows.filter((row) => row.owner_id === user.id)
        : rows,
    [rows, scope, user],
  );

  const quickCount = (key: QuickFilter) => {
    const actions = QUICK_ACTIONS[key];
    if (!actions) return scoped.length;
    return scoped.filter((row) => actions.includes(row.next_action)).length;
  };

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc((v) => !v);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const toggleExpanded = (row: RFQListItem) => {
    if (expanded === row.id) {
      setExpanded(null);
      return;
    }
    setExpanded(row.id);
    if (dialogues[row.id] || dialogueLoading === row.id) return;
    setDialogueLoading(row.id);
    api
      .communicationOverview(row.id)
      .then((overview: CommunicationOverviewRead) => {
        setDialogues((current) => ({
          ...current,
          [row.id]: overview.conversations,
        }));
        setDialogueError((current) => {
          const next = { ...current };
          delete next[row.id];
          return next;
        });
      })
      .catch((caught) =>
        setDialogueError((current) => ({
          ...current,
          [row.id]: caught instanceof Error ? caught.message : String(caught),
        })),
      )
      .finally(() => setDialogueLoading(null));
  };

  const exportCsv = () => {
    const header = [
      "№",
      "Вещество",
      "CAS",
      "Стадия",
      "Статус",
      "Что сделать",
      "Разослано",
      "Ответили",
      "Молчат",
      "Ждут ответа",
      "Ошибки доставки",
      "Полнота, %",
      "Дата рассылки",
      "Дней ожидания",
      "Создан",
    ];
    if (showOwner) header.push("Ответственный");
    const lines = [header.join(";")];
    for (const r of filtered) {
      const row = [
        r.id,
        `"${r.name.replace(/"/g, '""')}"`,
        r.cas ?? "",
        STAGE_LABELS[r.stage] ?? r.stage,
        STATUS_LABELS[r.status],
        `"${actionChip(r).label}"`,
        r.n_recipients,
        r.n_suppliers_replied,
        r.n_silent,
        r.n_awaiting_our_reply,
        r.n_dispatch_errors,
        r.completeness_pct,
        r.dispatched_at ? formatDate(r.dispatched_at) : "",
        r.waiting_days ?? "",
        formatDate(r.created_at),
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

  // Полоска конвейера: пройденные сегменты залиты, текущий подсвечен.
  const stageStrip = (stage: RFQStage) => {
    const reached = STAGE_ORDER.indexOf(stage);
    return (
      <div className="stage-strip" title={`Стадия: ${STAGE_LABELS[stage] ?? stage}`}>
        <span className="stage-steps" aria-hidden="true">
          {STAGE_ORDER.map((key, index) => (
            <span
              key={key}
              className={`stage-step${index < reached ? " done" : ""}${
                index === reached ? " current" : ""
              }`}
            />
          ))}
        </span>
        <span className="stage-name">{STAGE_LABELS[stage] ?? stage}</span>
      </div>
    );
  };

  // Охват переписки. «Ответили» считается по компаниям, а не по котировкам:
  // одна компания присылает несколько котировок, а вопрос без цены не
  // создаёт ни одной, хотя переписка уже идёт.
  const dialogueCell = (r: RFQListItem) => {
    if (r.n_recipients === 0) {
      return (
        <span className="dialogue-empty">
          {r.n_suppliers_found > 0
            ? `найдено ${r.n_suppliers_found}, не разослано`
            : "не разослано"}
        </span>
      );
    }
    return (
      <div className="dialogue-counts">
        <span className="dialogue-total">разослано {r.n_recipients}</span>
        <span className="dialogue-parts">
          <span className={r.n_suppliers_replied > 0 ? "replied" : "muted"}>
            ответили {r.n_suppliers_replied}
          </span>
          {r.n_silent > 0 && (
            <span className={r.n_silent * 2 > r.n_recipients ? "silent" : "muted"}>
              молчат {r.n_silent}
            </span>
          )}
        </span>
        {r.n_dispatch_errors > 0 && (
          <span className="dialogue-error">
            не ушло писем: {r.n_dispatch_errors}
          </span>
        )}
      </div>
    );
  };

  const timelineCell = (r: RFQListItem) => {
    if (!r.dispatched_at) {
      return (
        <span className="muted" title={`Заведён ${formatMoment(r.created_at)}`}>
          заведён {formatShortDate(r.created_at)}
        </span>
      );
    }
    return (
      <div className="request-timeline">
        <span title={`Первая отправка: ${formatMoment(r.dispatched_at)}`}>
          разослано {formatShortDate(r.dispatched_at)}
        </span>
        {r.last_inbound_at ? (
          <span
            className="muted"
            title={`Последний ответ: ${formatMoment(r.last_inbound_at)}`}
          >
            ответ {agoLabel(r.last_inbound_at)}
          </span>
        ) : (
          <span className={r.next_action === "silence" ? "silent" : "muted"}>
            {r.waiting_days === null
              ? "ответов нет"
              : `тишина ${days(r.waiting_days)}`}
          </span>
        )}
      </div>
    );
  };

  const dialogueRow = (r: RFQListItem) => {
    const conversations = dialogues[r.id];
    const failure = dialogueError[r.id];
    const columns = 5 + (showOwner ? 1 : 0) + (showDeleteAction ? 1 : 0);
    return (
      <tr className="dialogue-row" key={`${r.id}-dialogue`}>
        <td colSpan={columns}>
          {dialogueLoading === r.id && <p className="note">Загрузка переписки…</p>}
          {failure && <p className="error">{failure}</p>}
          {conversations && conversations.length === 0 && (
            <p className="note">Переписки по этому запросу ещё нет.</p>
          )}
          {conversations && conversations.length > 0 && (
            <ul className="dialogue-list">
              {conversations.map((item) => {
                const state =
                  COLLECTION_LABELS[item.data_collection_status] ??
                  COLLECTION_LABELS.not_started;
                return (
                  <li key={`${item.supplier_id ?? item.contact}-${item.channel}`}>
                    <span className="dialogue-company">{item.supplier_company}</span>
                    <span className="dialogue-channel">
                      {CHANNEL_LABELS[item.channel] ?? item.channel}
                    </span>
                    <span className={`badge ${state.tone}`}>{state.label}</span>
                    <span className="dialogue-when">
                      {item.last_message_at
                        ? `последнее сообщение ${agoLabel(item.last_message_at)}`
                        : "сообщений нет"}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </td>
      </tr>
    );
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
            label="Что сделать"
            options={ALL_ACTIONS.map((value) => ({
              value,
              label: ACTION_FILTER_LABELS[value],
            }))}
            values={actionFilters}
            onChange={setActionFilters}
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
              ["todo", "Требуют действия"],
              ["reply", "Ждут нашего ответа"],
              ["silence", "Молчат"],
              ["undispatched", "Не разослано"],
              ["decide", "Готовы к решению"],
            ] as [QuickFilter, string][]
          ).map(([key, label]) => {
            const count = quickCount(key);
            return (
              <button
                key={key}
                className={`chip ${quick === key ? "active" : ""} ${
                  count === 0 && key !== "all" ? "empty" : ""
                }`}
                onClick={() => setQuick(key)}
              >
                {label} <span className="chip-count">{count}</span>
              </button>
            );
          })}
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
          <table className="summary requests-table mobile-cards">
            <thead>
              <tr>
                <th onClick={() => toggleSort("id")}>№{arrow("id")}</th>
                <th onClick={() => toggleSort("name")}>
                  Вещество / стадия{arrow("name")}
                </th>
                <th onClick={() => toggleSort("action")}>Что сделать{arrow("action")}</th>
                <th>Переписка</th>
                <th onClick={() => toggleSort("dispatched_at")}>
                  Сроки{arrow("dispatched_at")}
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
              {filtered.flatMap((r) => {
                const chip = actionChip(r);
                const main = (
                  <tr key={r.id} className="clickable" onClick={() => onOpen(r.id)}>
                    <td data-label="№">{r.id}</td>
                    <td data-label="Вещество / стадия">
                      <div>{r.name}</div>
                      {r.cas && <div className="cas">CAS {r.cas}</div>}
                      {stageStrip(r.stage)}
                    </td>
                    <td data-label="Что сделать">
                      {/* Обёртка нужна карточке на телефоне: там подпись и
                          значение — соседи по flex-строке, и без неё чип
                          с пояснением разъезжались по разным краям. */}
                      <div className="action-cell">
                        <span
                          className={`action-chip tone-${ACTION_TONE[r.next_action]}`}
                          title={`Статус запроса: ${STATUS_LABELS[r.status]}`}
                        >
                          {chip.label}
                        </span>
                        {chip.detail && (
                          <div className="action-detail">{chip.detail}</div>
                        )}
                      </div>
                    </td>
                    <td data-label="Переписка">
                      <div className="dialogue-cell">
                        {dialogueCell(r)}
                        {r.n_recipients > 0 && (
                          <button
                            aria-expanded={expanded === r.id}
                            className="dialogue-toggle"
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              toggleExpanded(r);
                            }}
                          >
                            {expanded === r.id ? "свернуть" : "с кем переписка"}
                          </button>
                        )}
                      </div>
                    </td>
                    <td className="request-date" data-label="Сроки">
                      {timelineCell(r)}
                    </td>
                    {showOwner && <td data-label="Ответственный">{r.owner_name ?? "—"}</td>}
                    {showDeleteAction && (
                      <td className="request-actions-column" data-label="Действия">
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
                );
                return expanded === r.id ? [main, dialogueRow(r)] : [main];
              })}
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
