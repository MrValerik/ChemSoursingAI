// Карточка запроса (раздел 7 UI/UX-плана): слева параметры и статус-конвейер,
// справа вкладки полного пути обработки. Кнопка «Передать в ручной разбор»
// доступна на любом шаге.

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, userErrorMessage } from "../api/client";
import type { PriceHistoryItem, RFQRead, SearchRunListItem } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import DispatchTab from "./DispatchTab";
import DocumentsSection from "./DocumentsSection";
import SupplierSearchSection from "./SupplierSearchSection";
import SuppliersTab from "./SuppliersTab";
import Summary from "./Summary";
import { FieldSourceBadge, VerificationNotice } from "./SubstanceProvenance";
import { STATUS_LABELS, STATUS_TONE } from "./statusLabels";
import { Term } from "./ui";

type TabKey =
  | "overview"
  | "supplier_search"
  | "suppliers"
  | "dispatch"
  | "summary";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Обзор" },
  { key: "supplier_search", label: "Поиск и проверка" },
  { key: "suppliers", label: "Отобранные поставщики" },
  { key: "dispatch", label: "Общение" },
  { key: "summary", label: "Сводная таблица" },
];

// Статус → пройденные этапы конвейера (раздел 7: «Этапы»).
const STAGES = ["Проверка вещества", "Поиск", "Отбор", "Общение", "Ответы", "Сравнение"];
// Куда ведёт клик по проблемному этапу: вкладка с местом ошибки.
// Этап «Ответы» ведёт в «Общение»: отдельной вкладки ответов больше нет.
const STAGE_TABS: TabKey[] = [
  "supplier_search",
  "supplier_search",
  "suppliers",
  "dispatch",
  "dispatch",
  "summary",
];
const STAGE_BY_STATUS: Record<string, number> = {
  draft: 0,
  verified: 1,
  sent: 4,
  collecting: 4,
  parsed: 5,
  summarized: 6,
  escalated: 5,
  closed: 6,
};

const ESCALATION_REASONS: [string, string][] = [
  ["other", "Другое"],
  ["grade", "Нестандартный грейд"],
  ["logistics", "Опасная логистика"],
  ["shortage", "Дефицит"],
  ["custom_synthesis", "Кастомный синтез"],
];

const COUNTRY_LABELS: Record<string, string> = {
  russia: "Россия",
  "russian federation": "Россия",
  ru: "Россия",
  china: "Китай",
  cn: "Китай",
  prc: "Китай",
  india: "Индия",
  in: "Индия",
};

const INCOTERM_HELP: Record<string, string> = {
  CIP: "CIP: продавец оплачивает перевозку и страхование до согласованного пункта, но риск переходит при передаче первому перевозчику.",
  FCA: "FCA: продавец передаёт товар перевозчику покупателя в согласованном месте.",
  EXW: "EXW: покупатель забирает товар со склада или площадки продавца и организует основную перевозку.",
};

const displayCountry = (value: string) =>
  COUNTRY_LABELS[value.trim().toLocaleLowerCase()] || value.trim();

const purityHelp = (value: string) => {
  const normalized = value.trim().toLocaleUpperCase();
  if (normalized === "USP") {
    return "USP — требования Фармакопеи США (United States Pharmacopeia) к качеству и чистоте вещества. Соответствие должно подтверждаться спецификацией и CoA поставщика.";
  }
  if (["EP", "PH. EUR.", "PH EUR"].includes(normalized)) {
    return "EP / Ph. Eur. — требования Европейской фармакопеи. Соответствие проверяется по спецификации и CoA поставщика.";
  }
  if (normalized === "BP") {
    return "BP — требования Британской фармакопеи. Соответствие проверяется по спецификации и CoA поставщика.";
  }
  if (normalized === "ACS") {
    return "ACS — требования Американского химического общества к реактивному грейду вещества.";
  }
  return `«${value}» — требуемый грейд или стандарт чистоты, указанный в запросе. Его необходимо подтвердить документами поставщика.`;
};

export default function RfqDetail({
  rfq,
  onBack,
  onChanged,
  onOpenSubstance,
}: {
  rfq: RFQRead;
  onBack: () => void;
  onChanged: (r: RFQRead) => void;
  onOpenSubstance?: (id: number) => void;
}) {
  const { user } = useAuth();
  // Вкладка живёт в адресе: /requests/42/dispatch открывается по ссылке и
  // переживает перезагрузку. Неизвестное имя откатывается к поиску.
  const { tab: tabParam } = useParams();
  const navigate = useNavigate();
  const tab: TabKey = TABS.some((item) => item.key === tabParam)
    ? (tabParam as TabKey)
    : "supplier_search";
  const setTab = (key: TabKey) => navigate(`/requests/${rfq.id}/${key}`);
  const [searchRuns, setSearchRuns] = useState<SearchRunListItem[]>([]);

  const [escOpen, setEscOpen] = useState(false);
  const [escReason, setEscReason] = useState("other");
  const [escNote, setEscNote] = useState("");
  const [escBusy, setEscBusy] = useState(false);
  const [escError, setEscError] = useState<string | null>(null);

  const canEscalate = user?.role !== "auditor" && rfq.status !== "escalated";
  const doneStages = STAGE_BY_STATUS[rfq.status] ?? 0;

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const items = await api.listSearchRuns(50, rfq.id);
        if (active) setSearchRuns(items);
      } catch {
        // Статус карточки не должен ломаться из-за временной ошибки polling.
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [rfq.id]);

  const searchTerminal = new Set([
    "search_completed",
    "completed",
    "failed",
    "cancelled",
  ]);
  const searchFinished =
    searchRuns.length > 0 &&
    searchRuns.every((run) => searchTerminal.has(run.status));
  const searchSucceeded = searchRuns.some((run) =>
    ["search_completed", "completed"].includes(run.status),
  );
  const searchFailed =
    searchRuns.length > 0 && searchRuns.every((run) => run.status === "failed");
  const actualSearchCountries = [
    ...new Set(
      [
        ...(rfq.search_countries ?? []),
        ...searchRuns.map((run) => {
          const country = run.input_payload.country;
          return typeof country === "string" ? country : "";
        }),
      ]
        .map(displayCountry)
        .filter(Boolean),
    ),
  ];
  const incoterms = rfq.incoterms ?? [];
  const incotermsExplanation =
    incoterms
      .map((code) => INCOTERM_HELP[code.toLocaleUpperCase()])
      .filter(Boolean)
      .join(" ") ||
    "Условия поставки ещё не заданы.";

  const stageClass = (index: number) => {
    if (index === 0) {
      const searchStarted = searchRuns.some(
        (run) => !["queued", "identifying"].includes(run.status),
      );
      return rfq.verified || searchStarted || searchRuns.length > 0
        ? "done"
        : "current";
    }
    if (index === 1) {
      if (searchFailed) return "error";
      if (searchFinished && searchSucceeded) return "done";
      if (searchRuns.length > 0 || doneStages >= 1) return "current";
      return "";
    }
    return index < doneStages ? "done" : index === doneStages ? "current" : "";
  };

  const escalate = async () => {
    setEscBusy(true);
    setEscError(null);
    try {
      await api.escalateRfq(rfq.id, escReason, escNote.trim() || null);
      setEscOpen(false);
      setEscNote("");
      onChanged(await api.getRfq(rfq.id));
    } catch (e) {
      setEscError(e instanceof Error ? e.message : String(e));
    } finally {
      setEscBusy(false);
    }
  };

  return (
    <div className="requests-page">
      <div className="detail-header">
        <button className="secondary back-btn" onClick={onBack}>
          ← К запросам
        </button>
        <h1>
          Запрос #{rfq.id} · {rfq.name}
        </h1>
        <span className={`badge tone-${STATUS_TONE[rfq.status]}`}>
          {STATUS_LABELS[rfq.status]}
        </span>
        {canEscalate && (
          <button
            className="secondary escalate-btn"
            onClick={() => setEscOpen((v) => !v)}
          >
            Передать в ручной разбор
          </button>
        )}
      </div>

      {escOpen && (
        <div className="panel escalate-form">
          <h2>Передача в ручной разбор</h2>
          <div className="row">
            <div className="field">
              <label>Причина</label>
              <select value={escReason} onChange={(e) => setEscReason(e.target.value)}>
                {ESCALATION_REASONS.map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: 2 }}>
              <label>Комментарий</label>
              <input
                value={escNote}
                onChange={(e) => setEscNote(e.target.value)}
                placeholder="Что требует внимания специалиста"
              />
            </div>
          </div>
          {escError && <p className="error">{escError}</p>}
          <div className="actions">
            <button onClick={() => void escalate()} disabled={escBusy}>
              {escBusy ? "Передача…" : "Передать"}
            </button>
            <button className="secondary" onClick={() => setEscOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="detail-layout">
        <aside className="detail-params panel">
          <h2>
            <Term
              label="Параметры"
              help="Основные условия закупочного запроса. Подсказки открываются при наведении на подчёркнутые термины и значения."
            />
          </h2>
          <dl className="params-list">
            <dt className="param-label">
              <Term
                label="CAS"
                help="CAS Registry Number — уникальный числовой идентификатор химического вещества. Он помогает отличать вещества с похожими названиями."
              />
            </dt>
            <dd className="param-value">
              {rfq.cas ? (
                <>
                  <Term
                    label={rfq.cas}
                    help={
                      rfq.verified
                        ? `CAS ${rfq.cas} прошёл автоматическую проверку. Перед закупочным решением всё равно сравните его со спецификацией и CoA.`
                        : `CAS ${rfq.cas} ещё не подтверждён справочником или решением специалиста. Результаты поиска следует считать предварительными.`
                    }
                  />
                  {rfq.verified ? (
                    <span className="badge tone-ok">проверен</span>
                  ) : (
                    <span className="badge tone-neutral">не проверен</span>
                  )}
                  <FieldSourceBadge source={rfq.field_sources?.cas ?? null} />
                </>
              ) : (
                // Номера нет — это не пробел в данных, а способ задать
                // предмет закупки. Показываем какой.
                <Term
                  label={
                    rfq.identification_method === "analog"
                      ? `аналог: ${rfq.analog_reference ?? "—"}`
                      : "по спецификации"
                  }
                  help="У этого запроса нет CAS-номера: так закупают смеси, рецептуры и промышленные продукты, которым номер не присваивается. Поиск ведётся по подтверждённым названиям."
                />
              )}
            </dd>
            {rfq.purity && (
              <>
                <dt className="param-label">
                  <Term
                    label="Чистота"
                    help="Требуемая степень чистоты, фармакопейный стандарт или промышленный грейд вещества."
                  />
                </dt>
                <dd className="param-value">
                  <Term label={rfq.purity} help={purityHelp(rfq.purity)} />
                </dd>
              </>
            )}
            {rfq.volume && (
              <>
                <dt className="param-label">
                  <Term
                    label="Объём"
                    help="Количество вещества, которое планируется запросить у поставщиков."
                  />
                </dt>
                <dd className="param-value">
                  <Term
                    label={rfq.volume}
                    help={`Для этого запроса требуется ${rfq.volume}. Поставщик должен отдельно подтвердить MOQ и доступный объём.`}
                  />
                </dd>
              </>
            )}
            {rfq.target_price != null && (
              <>
                <dt className="param-label">
                  <Term
                    label="Ориентир"
                    help="Внутренний ценовой ориентир для сравнения предложений. Он не является заказом или обещанием поставщику."
                  />
                </dt>
                <dd className="param-value">
                  <Term
                    label={`${rfq.target_price} ${rfq.currency}`}
                    help={`Ценовой ориентир запроса: ${rfq.target_price} ${rfq.currency}. Для сравнения нужно учитывать единицу цены и базис поставки.`}
                  />
                </dd>
              </>
            )}
            <dt className="param-label">
              <Term
                label="Базисы"
                help="Incoterms определяют распределение расходов, обязанностей и рисков между продавцом и покупателем."
              />
            </dt>
            <dd className="param-value">
              <Term
                label={incoterms.join(", ") || "—"}
                help={incotermsExplanation}
              />
            </dd>
            <dt className="param-label">
              <Term
                label="Страны поиска"
                help="Страны, в которых запускался автоматический поиск поставщиков для этого запроса."
              />
            </dt>
            <dd className="param-value">
              <Term
                label={
                  actualSearchCountries.join(", ") || "поиск не запускался"
                }
                help={
                  actualSearchCountries.length > 0
                    ? `Фактически созданы запуски поиска: ${actualSearchCountries.join(", ")}. Повторно добавленная страна появляется здесь после постановки задачи в очередь.`
                    : "Для запроса пока нет ни одного запуска поиска."
                }
              />
            </dd>
            {rfq.owner_name && (
              <>
                <dt className="param-label">
                  <Term
                    label="Ответственный"
                    help="Сотрудник, отвечающий за ведение запроса и проверку результатов."
                  />
                </dt>
                <dd className="param-value">
                  <Term
                    label={rfq.owner_name}
                    help={`${rfq.owner_name} назначен ответственным за этот закупочный запрос.`}
                  />
                </dd>
              </>
            )}
          </dl>

          <h2 style={{ marginTop: 16 }}>Этапы</h2>
          <ol className="stages">
            {STAGES.map((s, i) => {
              const stateClass = stageClass(i);
              const needsAttention = stateClass === "error";
              return (
                <li key={s} className={stateClass}>
                  {needsAttention ? (
                    <button
                      className="stage-jump"
                      onClick={() => setTab(STAGE_TABS[i])}
                      title="Перейти к месту ошибки"
                      type="button"
                    >
                      {s}
                    </button>
                  ) : (
                    s
                  )}
                </li>
              );
            })}
          </ol>
          {rfq.status === "escalated" && (
            <p className="note esc-note">Запрос передан в ручной разбор.</p>
          )}
        </aside>

        <div className="detail-main">
          <div className="tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`tab ${tab === t.key ? "active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "overview" && <OverviewTab rfq={rfq} />}
          {tab === "supplier_search" && (
            <SupplierSearchSection
              rfq={rfq}
              onOpenSubstance={onOpenSubstance}
            />
          )}

          {tab === "suppliers" && (
            <SuppliersTab rfqId={rfq.id} onGoToDispatch={() => setTab("dispatch")} />
          )}

          {/* Рассылка, пришедшие ответы и документы — один рабочий поток. */}
          {tab === "dispatch" && (
            <>
              <DispatchTab
                rfq={rfq}
                onGoToSuppliers={() => setTab("suppliers")}
                onStatusChanged={() => {
                  void api.getRfq(rfq.id).then(onChanged);
                }}
              />
              <DocumentsSection rfqId={rfq.id} />
            </>
          )}

          {tab === "summary" && <Summary rfqId={rfq.id} />}
        </div>
      </div>
    </div>
  );
}

function OverviewTab({ rfq }: { rfq: RFQRead }) {
  const [history, setHistory] = useState<PriceHistoryItem[] | null>(null);

  useEffect(() => {
    // История цен ключуется номером: без него сопоставлять нечего.
    if (!rfq.cas) {
      setHistory([]);
      return;
    }
    api
      .priceHistory(rfq.cas)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [rfq.cas]);

  const v = rfq.verification;
  // История без котировок текущего запроса.
  const past = (history ?? []).filter((h) => h.rfq_id !== rfq.id);

  return (
    <>
      {/* Проверять нечего, если номера нет: запрос по аналогу или
          спецификации PubChem подтвердить не может в принципе. */}
      {rfq.cas && (
      <div className="panel">
        <h2>
          Верификация вещества{" "}
          <span className="badge tone-neutral" title="Проверка химических данных по CAS">
            PubChem
          </span>
        </h2>
        <VerificationNotice outcome={v?.outcome ?? null} />
        {v?.found ? (
          <dl className="params-list">
            <dt>Название (IUPAC)</dt>
            <dd>{(v.iupac_name as string) ?? "—"}</dd>
            <dt>Формула</dt>
            <dd>{(v.molecular_formula as string) ?? "—"}</dd>
            <dt>Молекулярная масса</dt>
            <dd>{(v.molecular_weight as number) ?? "—"}</dd>
            <dt>Синонимы</dt>
            <dd>{((v.synonyms as string[]) ?? []).slice(0, 6).join(", ") || "—"}</dd>
            <dt>Источник</dt>
            <dd>{(v.source as string) ?? "PubChem"}</dd>
          </dl>
        ) : (
          <p className="note">
            Проверка выполняется при создании запроса
            {v?.error ? ` (${userErrorMessage(v.error)})` : ""}.
          </p>
        )}
      </div>
      )}

      {rfq.cas && (
      <div className="panel">
        <h2>История закупочных цен по CAS {rfq.cas}</h2>
        {history === null && <p className="note">Загрузка…</p>}
        {history !== null && past.length === 0 && (
          <p className="note">Прошлых закупок по этому веществу не найдено.</p>
        )}
        {past.length > 0 && (
          <table className="summary">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Цена</th>
                <th>Базис</th>
                <th>MOQ</th>
                <th>Запрос</th>
              </tr>
            </thead>
            <tbody>
              {past.map((h, i) => (
                <tr key={i}>
                  <td>{h.date}</td>
                  <td>
                    {h.price} {h.currency ?? ""}
                  </td>
                  <td>{h.incoterm ?? "—"}</td>
                  <td>{h.moq ?? "—"}</td>
                  <td>#{h.rfq_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      )}
    </>
  );
}
