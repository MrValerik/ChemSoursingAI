import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, userErrorMessage } from "../api/client";
import type {
  SearchScope,
  AgentRunRead,
  CasEvidenceStatus,
  CountryEvidenceStatus,
  EvidenceStatus,
  QualifiedSupplierType,
  RFQRead,
  SearchRunListItem,
  SearchRunTrace,
  SupplierQualificationResponse,
  SupplierRead,
  SupplierSearchResponse,
  SupplierSearchResult,
  SupplierVerificationStatus,
  SubstanceRecord,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  SEARCH_MODES,
  modeCompanies,
  modeFromCompanies,
  type SearchModeKey,
} from "./searchModes";
import { HelpTip, Icon, Input, Select, Textarea, Toast } from "./ui";

const TYPE_LABELS: Record<QualifiedSupplierType, string> = {
  manufacturer: "Производитель",
  distributor: "Дистрибьютор",
  unknown: "Не определено",
};

const CAS_LABELS: Record<CasEvidenceStatus, string> = {
  confirmed: "подтверждён",
  mentioned: "упомянут",
  not_found: "не найден",
  mismatch: "не совпадает",
};

// Колонка отвечает на вопрос «то ли это вещество», и называется так же.
// «Идентичность» этого не сообщала: слово из отчёта, а не из закупки.
// Значение зависит от того, есть ли в запросе номер: с номером сверяется
// он, без номера — название, состав и грейд.
const SUBSTANCE_CELL_WITH_CAS: Record<CasEvidenceStatus, string> = {
  confirmed: "CAS подтверждён",
  mentioned: "CAS упомянут",
  not_found: "CAS не найден",
  mismatch: "CAS не совпадает",
};

const SUBSTANCE_CELL_BY_NAME: Record<CasEvidenceStatus, string> = {
  confirmed: "то же вещество",
  mentioned: "упомянуто вскользь",
  not_found: "не подтверждено",
  mismatch: "другое вещество",
};

// Решение человека о компании: статус в реестре плюс отказ в рамках этого
// запроса. Два разных отказа: «не то вещество» не делает компанию плохой.
export type SupplierDecision = {
  supplier_id: number;
  qualification_status: string;
  excluded_here: boolean;
};

export type DecisionAction =
  | "verified"
  | "under_review"
  | "candidate"
  | "rejected"
  | "exclude_here"
  | "return_here";

const DECISION_NOTICES: Record<DecisionAction, string> = {
  verified: "Компания подтверждена как поставщик.",
  under_review: "Компания отправлена на проверку.",
  candidate: "Компания снова в кандидатах.",
  rejected: "Компания исключена из реестра — во всех запросах.",
  exclude_here: "Компания вычеркнута из этого запроса; в реестре осталась.",
  return_here: "Компания снова участвует в этом запросе.",
};

const DECISION_LABELS: Record<string, string> = {
  verified: "подтверждён",
  under_review: "на проверке",
  rejected: "исключён из реестра",
};

const COMPANY_LEGAL_TAILS = [
  "coltd", "co", "ltd", "limited", "inc", "llc", "gmbh",
  "corporation", "corp", "group", "company", "plc", "sa", "bv",
  "pvt", "ag", "kg",
] as const;

// То же сравнение имён, которым backend объединяет карточки одной компании.
// Оно нужно для старых результатов, у которых в реестре мог сохраниться URL
// другого запуска того же поставщика.
const companyKey = (name: string) => {
  let collapsed = name
    .toLocaleLowerCase("ru")
    .replace(/[^0-9a-zа-яё\u4e00-\u9fff]+/giu, "");
  let changed = true;
  while (changed) {
    changed = false;
    for (const tail of COMPANY_LEGAL_TAILS) {
      if (collapsed.endsWith(tail) && collapsed.length > tail.length + 2) {
        collapsed = collapsed.slice(0, -tail.length);
        changed = true;
      }
    }
  }
  return collapsed;
};

// Сколько «замечаний» — число само по себе не говорит, чего именно.
const riskWord = (count: number) => {
  const tail = count % 10;
  const teen = count % 100;
  if (teen >= 11 && teen <= 14) return "замечаний";
  if (tail === 1) return "замечание";
  if (tail >= 2 && tail <= 4) return "замечания";
  return "замечаний";
};

const COUNTRY_LABELS: Record<CountryEvidenceStatus, string> = {
  claimed: "страна заявлена",
  likely: "страна вероятна",
  not_found: "страна не указана",
  mismatch: "другая страна",
};

const EVIDENCE_LABELS: Record<EvidenceStatus, string> = {
  claimed: "заявлено",
  not_found: "не найдено",
  contradicted: "есть противоречие",
};

const VOLUME_LABELS = {
  compatible: "совместим",
  incompatible: "несовместим",
  unknown: "нет подтверждённых данных",
} as const;

const supplyFactsLabel = (
  result: SupplierQualificationResponse["results"][number],
) => {
  const compatibility = result.volume_compatibility;
  if (!compatibility) return "не найдены";
  const parts: string[] = [];
  if (compatibility.found_packaging.length > 0) {
    parts.push(
      `фасовка ${compatibility.found_packaging.map((item) => item.raw).join(", ")}`,
    );
  }
  if (compatibility.moqs.length > 0) {
    parts.push(`MOQ ${compatibility.moqs.map((item) => item.raw).join(", ")}`);
  }
  if (compatibility.order_ranges.length > 0) {
    parts.push(
      `диапазон ${compatibility.order_ranges
        .map((item) => `${item.minimum.raw}–${item.maximum.raw}`)
        .join(", ")}`,
    );
  }
  return parts.join("; ") || "не найдены";
};

// «Аудитор» в закупке химии — это внешний GMP-аудит предприятия, а здесь
// речь о втором автоматическом проходе по тем же цитатам. Совпадение слова
// обещало закупщику подтверждение, которого никто не давал.
const VERIFICATION_LABELS: Record<SupplierVerificationStatus, string> = {
  confirmed: "Повторная проверка подтвердила",
  needs_review: "Повторная проверка требует человека",
  rejected: "Повторная проверка отклонила",
  unavailable: "Повторная проверка не выполнена",
};

const verificationTone = (status: SupplierVerificationStatus) => {
  if (status === "confirmed") return "tone-ok";
  if (status === "rejected") return "tone-danger";
  if (status === "needs_review") return "tone-warn";
  return "tone-neutral";
};

const DOCUMENT_FIELDS = [
  { key: "gmp_status", label: "GMP" },
  { key: "iso_status", label: "ISO" },
  { key: "coa_status", label: "CoA" },
  { key: "tds_status", label: "TDS" },
] as const;

const SOURCE_LABELS: Record<SupplierSearchResult["source_kind"], string> = {
  echemi: "Echemi",
  india_registry: "Официальный источник Индии",
  india_web: "Индийский сайт",
  web: "Открытый веб",
};

const SEARCH_STRATEGY_LABELS: Record<
  SupplierSearchResponse["search_strategy"],
  string
> = {
  direct_sites_first: "сайты компаний в первую очередь",
  echemi_first: "Echemi в первую очередь",
};

const sourceTone = (source: SupplierSearchResult["source_kind"]) => {
  if (source === "echemi") return "tone-ok";
  if (source === "india_registry") return "tone-ok";
  if (source === "india_web") return "tone-warn";
  return "tone-neutral";
};

const CLAIM_LABELS: Record<string, string> = {
  chemical_identity: "Совпадение вещества",
  manufacturer_role: "Роль производителя",
  country: "Страна",
  gmp: "GMP",
  iso: "ISO",
  coa: "CoA",
  tds: "TDS",
};

const evidenceExplanation = (
  result: SupplierQualificationResponse["results"][number],
  claimType: string,
  rule: string,
) => {
  const evidence = result.evidence.filter(
    (item) => item.claim_type === claimType,
  );
  if (evidence.length === 0) {
    return `${rule} Проверенной дословной цитаты на странице не найдено.`;
  }
  return `${rule} ${evidence
    .map(
      (item) =>
        `${item.support_status === "supports" ? "Подтверждение" : "Противоречие"}: «${item.quote}»`,
    )
    .join(" ")}`;
};

function EvidenceBadge({
  className,
  label,
  explanation,
}: {
  className: string;
  label: string;
  explanation: string;
}) {
  return (
    <span className="evidence-status">
      <span className={`badge ${className}`}>{label}</span>
      <HelpTip text={explanation} />
    </span>
  );
}

const scoreExplanation = (
  result: SupplierQualificationResponse["results"][number],
) =>
  [
    `Итог ${result.confidence}/100 =`,
    `вещество ${result.score_breakdown.identity}/35 +`,
    `роль компании ${result.score_breakdown.supplier_role}/25 +`,
    `страна ${result.score_breakdown.country}/10 +`,
    `документы ${result.score_breakdown.documents}/15 +`,
    `качество доказательств ${result.score_breakdown.evidence_quality}/15.`,
    `Поправка за подтверждение объёма ${result.score_breakdown.volume_adjustment ?? 0}.`,
    "Баллы начисляются только по дословно проверенным цитатам.",
    "При противоречии по веществу или CAS итоговый балл обнуляется.",
  ].join(" ");

const shortlistExplanation = (
  result: SupplierQualificationResponse["results"][number],
) => {
  const hasIdentity = result.evidence.some(
    (item) =>
      item.claim_type === "chemical_identity" &&
      item.support_status === "supports",
  );
  const hasManufacturerRole = result.evidence.some(
    (item) =>
      item.claim_type === "manufacturer_role" &&
      item.support_status === "supports",
  );
  return [
    "Компания готова к запросу, когда выполнено всё сразу:",
    `балл не ниже 70 (${result.confidence >= 70 ? "выполнено" : `сейчас ${result.confidence}`});`,
    `тип «Производитель» (${result.supplier_type === "manufacturer" ? "выполнено" : "не подтверждено"});`,
    `подтверждение вещества (${hasIdentity ? "есть" : "нет"});`,
    `подтверждение собственного производства (${hasManufacturerRole ? "есть" : "нет"});`,
    `промышленный объём (${
      result.volume_compatibility?.status === "compatible"
        ? "подтверждён"
        : result.volume_compatibility?.status === "incompatible"
          ? "несовместим"
          : "не подтверждён"
    });`,
    `повторная автоматическая проверка (${result.verification?.status === "confirmed" ? "подтвердила" : "не подтвердила"}).`,
  ].join(" ");
};

const verificationExplanation = (
  result: SupplierQualificationResponse["results"][number],
) => {
  if (!result.verification) {
    return (
      "Для этого сохранённого результата повторная проверка не выполнялась. " +
      "Кандидата нужно проверить руками."
    );
  }
  return [
    result.verification.reason,
    result.verification.gate_reason,
    `Уверенность повторной проверки: ${result.verification.confidence}%.`,
  ].join(" ");
};

// Короткая строка статуса для колонки таблицы: в ячейке нужно слово, по
// которому видно состояние, а объяснение ждёт в окне подробностей.
const documentsCell = (
  result: SupplierQualificationResponse["results"][number],
) => {
  const contradicted = DOCUMENT_FIELDS.filter(
    (document) => result[document.key] === "contradicted",
  ).map((document) => document.label);
  if (contradicted.length > 0) return `противоречие: ${contradicted.join(", ")}`;
  const claimed = DOCUMENT_FIELDS.filter(
    (document) => result[document.key] === "claimed",
  ).map((document) => document.label);
  return claimed.length > 0 ? claimed.join(", ") : "не найдены";
};

const documentsRank = (
  result: SupplierQualificationResponse["results"][number],
) => {
  if (DOCUMENT_FIELDS.some((d) => result[d.key] === "contradicted")) return 2;
  if (DOCUMENT_FIELDS.some((d) => result[d.key] === "claimed")) return 0;
  return 1;
};

const documentsTone = (
  result: SupplierQualificationResponse["results"][number],
) => {
  if (DOCUMENT_FIELDS.some((d) => result[d.key] === "contradicted")) return "danger";
  if (DOCUMENT_FIELDS.some((d) => result[d.key] === "claimed")) return "warn";
  return "muted";
};

type QualificationSortKey =
  | "company"
  | "type"
  | "confidence"
  | "cas"
  | "country"
  | "documents"
  | "risks";

// Сортировка по статусу идёт от «подтверждено» к «противоречию», а не по
// алфавиту: закупщику важна степень подтверждённости, а не название статуса.
const CAS_ORDER: Record<CasEvidenceStatus, number> = {
  confirmed: 0,
  mentioned: 1,
  not_found: 2,
  mismatch: 3,
};

const COUNTRY_ORDER: Record<CountryEvidenceStatus, number> = {
  claimed: 0,
  likely: 1,
  not_found: 2,
  mismatch: 3,
};

// Таблица найденных поставщиков.
//
// Закупщик сравнивает компании между собой, а не читает их по очереди: в
// таблице один и тот же параметр стоит в одном столбце, и восемь кандидатов
// сравниваются одним взглядом сверху вниз. Карточки требовали читать каждую
// целиком. Всё, что не помещается в строку, открывается по клику.
function QualificationTable({
  results,
  activeCas,
  activeCountry,
  onSelect,
  decisionFor,
  onDecide,
  busySupplierId,
  canDecide,
}: {
  results: SupplierQualificationResponse["results"];
  activeCas: string | null;
  activeCountry: string;
  onSelect: (result: SupplierQualificationResponse["results"][number]) => void;
  decisionFor: (
    result: SupplierQualificationResponse["results"][number],
  ) => SupplierDecision | null;
  onDecide: (decision: SupplierDecision, action: DecisionAction) => void;
  busySupplierId: number | null;
  canDecide: boolean;
}) {
  // Балл — то, ради чего список упорядочен, поэтому он и стоит по умолчанию,
  // от большего к меньшему.
  const [sortKey, setSortKey] = useState<QualificationSortKey>("confidence");
  const [sortAsc, setSortAsc] = useState(false);
  // Открытое меню действий: одновременно не больше одного на таблицу.
  const [menuFor, setMenuFor] = useState<string | null>(null);

  // Меню закрывается кликом мимо него: строка под ним кликабельна, и
  // оставленное открытым меню перехватывало бы нажатие.
  useEffect(() => {
    if (menuFor === null) return;
    const close = () => setMenuFor(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuFor]);

  // Две колонки называют то, чего не видно из самого значения: сколько
  // «баллов» и по какой шкале, и что считается идентичностью, когда
  // номера у продукта нет. Пояснение висит на значке у заголовка.
  const columns: {
    key: QualificationSortKey;
    label: string;
    hint?: string;
  }[] = [
    { key: "company", label: "Компания" },
    { key: "type", label: "Роль" },
    {
      key: "confidence",
      label: "Балл",
      hint:
        "Насколько компания подходит под запрос, от 0 до 100. Складывается " +
        "из того, что подтвердила её страница: вещество — до 35, роль " +
        "производителя — до 25, страна — до 10, документы — до 15, качество " +
        "самих доказательств — до 15. Баллы даются только за дословно " +
        "найденную цитату; при противоречии по веществу балл обнуляется. " +
        "Балл от 70 — одно из условий готовности к запросу.",
    },
    {
      key: "cas",
      label: "Вещество",
      hint: activeCas
        ? `То ли это вещество: сверяется номер CAS ${activeCas}. Подтверждён — номер найден на странице дословно; упомянут — номер есть, но рядом с другим продуктом; не найден; не совпадает — на странице другой номер.`
        : "То ли это вещество. Номера у смесей и промышленных марок нет, " +
          "поэтому сверяются название, состав и грейд: «то же вещество» — " +
          "страница подтверждает продукт, «упомянуто вскользь» — название " +
          "встречается, но продукт другой, «другое вещество» — противоречие.",
    },
    {
      key: "country",
      label: "Страна",
      hint: `Подтверждает ли страница компании связь со страной поиска (${activeCountry || "страна не выбрана"}). Название страны в ячейке — связь подтверждена самой страницей; «вероятно» — подтверждена косвенно, например доменом или адресом склада.`,
    },
    { key: "documents", label: "Документы" },
    {
      key: "risks",
      label: "Риски",
      hint:
        "Сомнительные места, найденные проверкой на странице компании: " +
        "заявления без подтверждения, отсутствие сертификатов, " +
        "противоречия требованиям запроса. Это не приговор, а список того, " +
        "о чём стоит спросить в переписке. Сами формулировки — в карточке " +
        "компании, строка открывается по нажатию.",
    },
  ];

  const sortBy = (key: QualificationSortKey) => {
    if (key === sortKey) {
      setSortAsc((prev) => !prev);
      return;
    }
    setSortKey(key);
    // Числовые колонки читают сверху вниз от большего, текстовые — от «а».
    setSortAsc(!["confidence", "risks"].includes(key));
  };

  const sorted = useMemo(() => {
    const value = (
      result: SupplierQualificationResponse["results"][number],
    ): string | number => {
      switch (sortKey) {
        case "company":
          return result.company_name.toLocaleLowerCase();
        case "type":
          return TYPE_LABELS[result.supplier_type];
        case "confidence":
          return result.confidence;
        case "cas":
          return CAS_ORDER[result.cas_status];
        case "country":
          return COUNTRY_ORDER[result.country_status];
        case "documents":
          return documentsRank(result);
        case "risks":
          return result.red_flags.length;
      }
    };
    return [...results].sort((a, b) => {
      const left = value(a);
      const right = value(b);
      if (left === right) {
        return (
          b.confidence - a.confidence ||
          a.company_name.localeCompare(b.company_name)
        );
      }
      const order =
        typeof left === "number" && typeof right === "number"
          ? left - right
          : String(left).localeCompare(String(right), "ru");
      return sortAsc ? order : -order;
    });
  }, [results, sortKey, sortAsc]);

  return (
    <div className="table-scroll">
      {/* Таблица шире телефона: прокручивается вбок внутри своей
          рамки, а не растягивает страницу. */}
      <table className="summary qualification-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>
                <span className="table-head-cell">
                  <button
                    type="button"
                    className="table-sort"
                    onClick={() => sortBy(column.key)}
                    aria-label={`Сортировать по «${column.label}»`}
                  >
                    {column.label}
                    <span className="table-sort-mark">
                      {sortKey === column.key ? (sortAsc ? "▲" : "▼") : ""}
                    </span>
                  </button>
                  {column.hint && <HelpTip text={column.hint} />}
                </span>
              </th>
            ))}
            {canDecide && <th className="qualification-actions-column"> </th>}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length + (canDecide ? 1 : 0)} className="note">
                Ни одна найденная страница компанию не назвала — всё найденное
                ниже, в отсеянном.
              </td>
            </tr>
          )}
          {sorted.map((result) => {
            const decision = decisionFor(result);
            const decisionLabel = decision
              ? decision.excluded_here
                ? "не для этого запроса"
                : DECISION_LABELS[decision.qualification_status]
              : undefined;
            return (
            <tr
              className={`clickable${decision?.excluded_here || decision?.qualification_status === "rejected" ? " row-declined" : ""}`}
              key={result.url}
              onClick={() => onSelect(result)}
              title="Открыть подробности: проверка, расчёт балла и цитаты"
            >
              <td>
                <strong>{result.company_name}</strong>
                {decisionLabel && (
                  <div
                    className={`decision-mark${
                      decision?.excluded_here ||
                      decision?.qualification_status === "rejected"
                        ? " is-declined"
                        : ""
                    }`}
                  >
                    {decisionLabel}
                  </div>
                )}
                {result.third_party && !result.winnowed && (
                  // Имя со справочника или обзора рынка верное, но сказано о
                  // компании с чужих слов, и почта на такой странице чужая.
                  // Прятать такую находку нельзя: она идёт в список компаний,
                  // и две вкладки разошлись бы. В отсеянных пометка не нужна:
                  // там компании нет вовсе, и говорить, откуда она взята, не о
                  // чем.
                  <div className="cas" title="Найдена не на сайте компании: справочник, обзор рынка, статья или перечень площадки. Контакты с такой страницы не берём — они принадлежат её владельцу.">
                    с чужой страницы
                  </div>
                )}
              </td>
              <td>
                <span
                  className={`badge ${
                    result.supplier_type === "manufacturer"
                      ? "tone-ok"
                      : result.supplier_type === "distributor"
                        ? "tone-warn"
                        : "tone-neutral"
                  }`}
                >
                  {TYPE_LABELS[result.supplier_type]}
                </span>
              </td>
              <td>
                <strong>{result.confidence}%</strong>
              </td>
              <td
                className={
                  result.cas_status === "confirmed"
                    ? "cell-ok"
                    : result.cas_status === "mismatch"
                      ? "cell-danger"
                      : "cell-muted"
                }
              >
                {activeCas
                  ? SUBSTANCE_CELL_WITH_CAS[result.cas_status]
                  : SUBSTANCE_CELL_BY_NAME[result.cas_status]}
              </td>
              <td
                className={
                  result.country_status === "claimed"
                    ? "cell-ok"
                    : result.country_status === "mismatch"
                      ? "cell-danger"
                      : "cell-muted"
                }
              >
                {/* Страна названа словом, а не статусом: закупщику нужна
                    страна, а «заявлена» без неё ничего не значит. Там, где
                    страницей она не подтверждена, называть её нельзя — стоит
                    причина. */}
                {result.country_status === "claimed" ||
                result.country_status === "likely" ? (
                  <>
                    <div>{activeCountry || "—"}</div>
                    {result.country_status === "likely" && (
                      <div className="cas">вероятно</div>
                    )}
                  </>
                ) : (
                  COUNTRY_LABELS[result.country_status]
                )}
              </td>
              <td className={`cell-${documentsTone(result)}`}>
                {documentsCell(result)}
              </td>
              <td className={result.red_flags.length > 0 ? "cell-danger" : "cell-muted"}>
                {result.red_flags.length > 0
                  ? `${result.red_flags.length} ${riskWord(result.red_flags.length)}`
                  : "нет"}
              </td>
              {canDecide && (
                <td
                  className="qualification-actions-column"
                  onClick={(event) => event.stopPropagation()}
                >
                  {decision ? (
                    <div className="row-menu">
                      <button
                        aria-label={`Действия с компанией ${result.company_name}`}
                        className="ui-icon-button row-menu-button"
                        disabled={busySupplierId === decision.supplier_id}
                        title="Действия с компанией"
                        type="button"
                        onClick={(event) => {
                          // Клик всплывает до окна, которое закрывает меню:
                          // без остановки оно закрылось бы тем же нажатием,
                          // которым открылось.
                          event.stopPropagation();
                          setMenuFor((current) =>
                            current === result.url ? null : result.url,
                          );
                        }}
                      >
                        ⋮
                      </button>
                      {menuFor === result.url && (
                        <div className="dropdown row-menu-dropdown">
                          <div className="dropdown-title">
                            {result.company_name}
                          </div>
                          {decision.qualification_status !== "verified" && (
                            <button
                              className="dropdown-item"
                              type="button"
                              onClick={() => onDecide(decision, "verified")}
                            >
                              Подтвердить поставщика
                            </button>
                          )}
                          {decision.qualification_status !== "under_review" && (
                            <button
                              className="dropdown-item"
                              type="button"
                              onClick={() => onDecide(decision, "under_review")}
                            >
                              Отправить на проверку
                            </button>
                          )}
                          {decision.qualification_status !== "candidate" && (
                            <button
                              className="dropdown-item"
                              type="button"
                              onClick={() => onDecide(decision, "candidate")}
                            >
                              Вернуть в кандидаты
                            </button>
                          )}
                          {decision.excluded_here ? (
                            <button
                              className="dropdown-item"
                              type="button"
                              onClick={() => onDecide(decision, "return_here")}
                            >
                              Вернуть в этот запрос
                            </button>
                          ) : (
                            <button
                              className="dropdown-item"
                              type="button"
                              onClick={() => onDecide(decision, "exclude_here")}
                            >
                              Не подходит для этого запроса
                            </button>
                          )}
                          {decision.qualification_status !== "rejected" && (
                            <button
                              className="dropdown-item is-danger"
                              type="button"
                              onClick={() => onDecide(decision, "rejected")}
                            >
                              Исключить из реестра
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    // Страница компанией не назвалась — в реестр её не
                    // сохраняли, и решать не о чем.
                    <span className="note" title="Страница не сохранена как компания">
                      —
                    </span>
                  )}
                </td>
              )}
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Подробности поставщика: то, что не помещается в строку таблицы.
//
// Здесь всё, чем вывод доказывается: суть, статусы с объяснениями, решение
// повторной проверки, правило отбора, расчёт балла, цитаты и исходная выдача.
function QualificationDetail({
  result,
  activeCas,
  activeCountry,
  trace,
  savedToRegistry,
}: {
  result: SupplierQualificationResponse["results"][number];
  activeCas: string | null;
  activeCountry: string;
  trace: SearchRunTrace | null;
  savedToRegistry: boolean;
}) {
  // Четыре отдельных тега документов занимали по строке каждый, хотя
  // отвечают на один вопрос: что поставщик о себе заявил и где противоречие.
  const claimedDocuments = DOCUMENT_FIELDS.filter(
    (document) => result[document.key] === "claimed",
  ).map((document) => document.label);
  const contradictedDocuments = DOCUMENT_FIELDS.filter(
    (document) => result[document.key] === "contradicted",
  ).map((document) => document.label);

  return (
    <div className="qualification-detail-content">
      <div className="qualification-card-header">
        <div>
          <span
            className={`badge ${
              result.supplier_type === "manufacturer"
                ? "tone-ok"
                : result.supplier_type === "distributor"
                  ? "tone-warn"
                  : "tone-neutral"
            }`}
          >
            {TYPE_LABELS[result.supplier_type]}
          </span>
          <h4>{result.title_ru}</h4>
        </div>
        <div className="confidence">
          <div className="confidence-value">
            <strong>{result.confidence}%</strong>
            <HelpTip text={scoreExplanation(result)} />
          </div>
          <span>балл по проверенным данным</span>
        </div>
      </div>

      <p className="qualification-summary">{result.summary_ru}</p>

      <div className="qualification-evidence">
        <EvidenceBadge
          className={
            result.cas_status === "confirmed"
              ? "tone-ok"
              : result.cas_status === "mismatch"
                ? "tone-danger"
                : "tone-neutral"
          }
          label={`Вещество: ${
            activeCas
              ? SUBSTANCE_CELL_WITH_CAS[result.cas_status]
              : SUBSTANCE_CELL_BY_NAME[result.cas_status]
          }`}
          explanation={evidenceExplanation(
            result,
            "chemical_identity",
            activeCas
              ? `Статус «${CAS_LABELS[result.cas_status]}» показывает, найдено ли на открытой первичной странице точное подтверждение CAS ${activeCas} и вещества.`
              : `Статус «${CAS_LABELS[result.cas_status]}» показывает, подтверждает ли первичная страница название, состав или требуемый грейд продукта без CAS. Для аналога дополнительно нужно вручную сравнить свойства.`,
          )}
        />
        <EvidenceBadge
          className={
            result.country_status === "claimed"
              ? "tone-ok"
              : result.country_status === "mismatch"
                ? "tone-danger"
                : result.country_status === "likely"
                  ? "tone-warn"
                  : "tone-neutral"
          }
          label={`${activeCountry}: ${COUNTRY_LABELS[result.country_status]}`}
          explanation={evidenceExplanation(
            result,
            "country",
            `Статус «${COUNTRY_LABELS[result.country_status]}» показывает, подтверждает ли первичная страница связь компании или производственной площадки со страной поиска.`,
          )}
        />
        <EvidenceBadge
          className={
            contradictedDocuments.length > 0
              ? "tone-danger"
              : claimedDocuments.length > 0
                ? "tone-warn"
                : "tone-neutral"
          }
          label={
            contradictedDocuments.length > 0
              ? `Документы: противоречие (${contradictedDocuments.join(", ")})`
              : claimedDocuments.length > 0
                ? `Документы заявлены: ${claimedDocuments.join(", ")}`
                : "Документы: не найдены"
          }
          explanation={DOCUMENT_FIELDS.map(
            (document) =>
              `${document.label} — ${EVIDENCE_LABELS[result[document.key]]}.`,
          )
            .concat(
              "Заявление на сайте не заменяет сам документ: его нужно запросить у поставщика.",
            )
            .join(" ")}
        />
        {result.volume_compatibility?.requested_volume_raw && (
          <EvidenceBadge
            className={
              result.volume_compatibility.status === "compatible"
                ? "tone-ok"
                : result.volume_compatibility.status === "incompatible"
                  ? "tone-danger"
                  : "tone-neutral"
            }
            label={`Объём: ${VOLUME_LABELS[result.volume_compatibility.status]}`}
            explanation={result.volume_compatibility.reason}
          />
        )}
      </div>

      {result.volume_compatibility?.requested_volume_raw && (
        <div className="note qualification-missing">
          <strong>Промышленный объём и фасовка</strong>
          <p>
            Требуется: {result.volume_compatibility.requested_volume_raw}. Найдено: {" "}
            {supplyFactsLabel(result)}.
          </p>
          <p>{result.volume_compatibility.reason}</p>
          {result.volume_compatibility.quote && (
            <blockquote>«{result.volume_compatibility.quote}»</blockquote>
          )}
          <a
            href={result.volume_compatibility.source_url || result.url}
            target="_blank"
            rel="noreferrer"
          >
            Источник фасовки и MOQ
          </a>
        </div>
      )}

      {/* Замечания приходят отдельными формулировками, и склеенные в один
          абзац через «;» читались как одно длинное предложение: где
          кончается первое и начинается второе, видно только по точке с
          запятой. Каждое — своя строка. */}
      {result.red_flags.length > 0 && (
        <div className="qualification-warning">
          <strong>
            Риски ({result.red_flags.length} {riskWord(result.red_flags.length)})
          </strong>
          <ul>
            {result.red_flags.map((flag) => (
              <li key={flag}>{flag.replace(/[;\s]+$/, "")}</li>
            ))}
          </ul>
        </div>
      )}
      {result.missing_evidence.length > 0 && (
        <div className="note qualification-missing">
          <strong>Запросить у поставщика</strong>
          <ul>
            {result.missing_evidence.map((item) => (
              <li key={item}>{item.replace(/[;\s]+$/, "")}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="qualification-card-footer">
        <a
          className="source-link"
          href={result.url}
          target="_blank"
          rel="noreferrer"
        >
          Открыть первичный источник
        </a>
        {savedToRegistry && (
          <span className="candidate-auto-saved">
            <Icon name="check" size={16} />
            Сохранён в реестре
          </span>
        )}
      </div>

      <div className="qualification-details-body">
        {/* «Короткий список» — слово из нашего конвейера, а не из закупки:
            никакого отдельного списка закупщик не ведёт. По делу здесь
            один вопрос — можно ли уже писать этой компании. */}
        <EvidenceBadge
          className={result.shortlist_eligible ? "tone-ok" : "tone-neutral"}
          label={
            result.shortlist_eligible
              ? "Готова к запросу"
              : "Ещё не готова к запросу"
          }
          explanation={shortlistExplanation(result)}
        />
        {/* Итог повторной проверки показывается, только когда он что-то
            меняет. Подтвердила — это уже сказано строкой выше, и второй
            бейдж рядом означал бы вторую, отдельную проверку. */}
        {(result.verification?.status ?? "unavailable") !== "confirmed" && (
          <EvidenceBadge
            className={verificationTone(
              result.verification?.status ?? "unavailable",
            )}
            label={
              VERIFICATION_LABELS[result.verification?.status ?? "unavailable"]
            }
            explanation={verificationExplanation(result)}
          />
        )}

        <ul className="score-list">
            <li>Совпадение вещества: {result.score_breakdown.identity}/35</li>
            <li>Роль компании: {result.score_breakdown.supplier_role}/25</li>
            <li>Страна: {result.score_breakdown.country}/10</li>
            <li>Документы: {result.score_breakdown.documents}/15</li>
            <li>
              Качество доказательств: {result.score_breakdown.evidence_quality}
              /15
            </li>
            {(result.score_breakdown.volume_adjustment ?? 0) !== 0 && (
              <li>
                Поправка за промышленный объём:{" "}
                {result.score_breakdown.volume_adjustment}
              </li>
            )}
          </ul>

          {result.evidence.length > 0 && (
            <div className="candidate-evidence-list">
              <strong>Проверенные цитаты ({result.evidence.length})</strong>
              {result.evidence.map((evidence) => {
                const source = trace?.source_documents.find(
                  (item) => item.id === evidence.source_document_id,
                );
                return (
                  <blockquote key={evidence.id}>
                    <span>
                      {CLAIM_LABELS[evidence.claim_type] || evidence.claim_type}:
                    </span>{" "}
                    «{evidence.quote}»
                    {source && (
                      <>
                        {" "}
                        <a
                          href={source.final_url || source.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          источник
                        </a>
                      </>
                    )}
                  </blockquote>
                );
              })}
            </div>
          )}

        <p className="note">
          Исходный фрагмент поисковой выдачи получен до проверки страницы и
          доказательством не считается: {result.title}. {result.snippet}
        </p>
      </div>
    </div>
  );
}

const formatJson = (value: unknown) => JSON.stringify(value, null, 2);

const traceTone = (status: string) => {
  if (status === "completed" || status === "search_completed") return "tone-ok";
  if (status === "failed") return "tone-danger";
  return "tone-warn";
};

// Статусы, после которых прогон уже ничего не сделает сам.
const FINISHED_SEARCH_STATUSES = [
  "completed",
  "search_completed",
  "failed",
  "cancelled",
];

const SEARCH_STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  identifying: "Проверка CAS и вещества",
  planning: "Планирование запросов",
  searching: "Поиск по источникам",
  search_completed: "Кандидаты найдены",
  fetching_sources: "Загрузка первичных страниц",
  qualifying: "Квалификация поставщиков",
  verifying: "Независимая проверка",
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Отменено",
};

const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

const PIPELINE_STEPS = [
  {
    slug: "substance_lookup",
    title: "Проверка вещества",
    description: "Сверяет CAS и основные сведения о веществе с внешним справочником.",
  },
  {
    slug: "substance_identity",
    title: "Уточнение названий",
    description: "Определяет синонимы и варианты названия для более полного поиска.",
  },
  {
    slug: "search_planner",
    title: "Подготовка стратегии",
    description: "ИИ-агент решает, какие типы источников и формулировки использовать.",
  },
  {
    slug: "web_search",
    title: "Поиск компаний",
    description: "Ищет производителей и поставщиков в открытых источниках.",
  },
  {
    slug: "source_fetch",
    title: "Проверка страниц",
    description: "Открывает первичные страницы компаний и сохраняет подтверждения.",
  },
  {
    slug: "supplier_qualification",
    title: "Оценка поставщиков",
    description: "Проверяет роль компании, страну, CAS, документы и возможные риски.",
  },
  {
    slug: "supplier_verifier",
    title: "Повторная проверка",
    description: "Второй раз сверяет вещество и роль производителя по тем же цитатам.",
  },
] as const;

const PURPOSE_LABELS: Record<string, string> = {
  manufacturer: "Поиск производителей",
  product: "Поиск страниц вещества",
  documents: "Поиск документов",
  registry: "Проверка реестров",
};

const LANGUAGE_LABELS: Record<string, string> = {
  en: "английский",
  zh: "китайский",
  ru: "русский",
  other: "другой язык",
};

const runText = (run: SearchRunListItem, key: string) => {
  const value = run.input_payload[key];
  return typeof value === "string" ? value : "";
};

const asRecord = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

const asArray = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];

const displayText = (value: unknown, fallback = "Не указано") =>
  typeof value === "string" && value.trim()
    ? value
    : typeof value === "number"
      ? String(value)
      : fallback;

const formatDuration = (latencyMs: number) => {
  const totalSeconds = Math.max(0, Math.round(latencyMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} мин ${seconds} сек`;
};

const formatTokens = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString("ru-RU");

/** Расход токенов всего запуска: этапы делают вызовы пакетами. */
const totalTokens = (stages: AgentRunRead[]) =>
  stages.reduce(
    (sum, stage) => ({
      input: sum.input + (stage.prompt_tokens ?? 0),
      output: sum.output + (stage.completion_tokens ?? 0),
    }),
    { input: 0, output: 0 },
  );

function StageResult({
  slug,
  output,
}: {
  slug: (typeof PIPELINE_STEPS)[number]["slug"];
  output: Record<string, unknown>;
}) {
  if (slug === "substance_lookup") {
    return (
      <div className="stage-result-grid">
        <div>
          <span>Статус проверки</span>
          <strong>{output.found ? "Вещество найдено" : "Не подтверждено"}</strong>
        </div>
        <div>
          <span>Наименование в справочнике</span>
          <strong>
            {displayText(output.iupac_name ?? output.title ?? output.name)}
          </strong>
        </div>
        <div>
          <span>Молекулярная формула</span>
          <strong>{displayText(output.molecular_formula)}</strong>
        </div>
        <div>
          <span>Молекулярная масса</span>
          <strong>{displayText(output.molecular_weight)}</strong>
        </div>
      </div>
    );
  }

  if (slug === "substance_identity") {
    const identity = asRecord(output.identity);
    const names = asArray(identity.search_names).filter(
      (item): item is string => typeof item === "string" && Boolean(item),
    );
    const ambiguities = asArray(identity.ambiguities).filter(
      (item): item is string => typeof item === "string" && Boolean(item),
    );
    return (
      <div className="stage-result-friendly">
        <div className="stage-result-hero">
          <span>Установленное наименование</span>
          <strong>{displayText(identity.canonical_name)}</strong>
        </div>
        {names.length > 0 && (
          <div>
            <span className="stage-result-label">Варианты для поиска</span>
            <div className="stage-result-tags">
              {names.map((name) => (
                <span key={name}>{name}</span>
              ))}
            </div>
          </div>
        )}
        {ambiguities.length > 0 && (
          <div className="stage-result-notice">
            <strong>Требует внимания</strong>
            {ambiguities.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (slug === "search_planner") {
    const queries = asArray(output.queries).map(asRecord);
    return (
      <div className="stage-result-friendly">
        <div className="stage-result-hero">
          <span>Подготовлено поисковых сценариев</span>
          <strong>{queries.length}</strong>
        </div>
        {queries.length > 0 && (
          <div className="stage-result-list">
            {queries.map((query, index) => (
              <div key={`${displayText(query.query)}-${index}`}>
                <span>{PURPOSE_LABELS[displayText(query.purpose, "")] || "Поиск компаний"}</span>
                <strong>{displayText(query.query)}</strong>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (slug === "web_search") {
    const queries = asArray(output.queries_used);
    const results = asArray(output.results);
    return (
      <div className="stage-result-grid">
        <div>
          <span>Выполнено запросов</span>
          <strong>{queries.length}</strong>
        </div>
        <div>
          <span>Уникальных кандидатов</span>
          <strong>{results.length}</strong>
        </div>
      </div>
    );
  }

  if (slug === "source_fetch") {
    const sources = asArray(output.sources).map(asRecord);
    const completed = sources.filter((source) => source.status === "completed").length;
    return (
      <div className="stage-result-grid">
        <div>
          <span>Страниц обработано</span>
          <strong>{sources.length}</strong>
        </div>
        <div>
          <span>Страниц доступно для проверки</span>
          <strong>{completed}</strong>
        </div>
      </div>
    );
  }

  const results = asArray(output.qualified_results).map(asRecord);
  const manufacturers = results.filter(
    (result) => result.supplier_type === "manufacturer",
  ).length;
  return (
    <div className="stage-result-grid">
      <div>
        <span>Компаний оценено</span>
        <strong>{results.length}</strong>
      </div>
      <div>
        <span>Вероятных производителей</span>
        <strong>{manufacturers}</strong>
      </div>
    </div>
  );
}

const qualificationFromTrace = (
  trace: SearchRunTrace,
): SupplierQualificationResponse | null => {
  if (trace.qualified_results.length === 0) return null;
  const qualificationStage = [...trace.agent_runs]
    .reverse()
    .find((item) => item.agent_slug === "supplier_qualification");
  const verificationStage = [...trace.agent_runs]
    .reverse()
    .find((item) => item.agent_slug === "supplier_verifier");
  const output =
    verificationStage?.output_payload ??
    qualificationStage?.output_payload ??
    {};
  const registryLinks = Array.isArray(output.registry_links)
    ? output.registry_links.filter(
        (item): item is { result_index: number; supplier_id: number } =>
          typeof item === "object" &&
          item !== null &&
          typeof (item as { result_index?: unknown }).result_index === "number" &&
          typeof (item as { supplier_id?: unknown }).supplier_id === "number",
      )
    : [];
  return {
    search_run_id: trace.id,
    results: trace.qualified_results,
    prompt_id: qualificationStage?.prompt_id ?? null,
    prompt_version: qualificationStage?.prompt_version ?? null,
    verification_prompt_id: verificationStage?.prompt_id ?? null,
    verification_prompt_version: verificationStage?.prompt_version ?? null,
    registry_links: registryLinks,
    requested_supplier_count:
      typeof output.requested_supplier_count === "number"
        ? output.requested_supplier_count
        : undefined,
    verified_source_count:
      typeof output.verified_source_count === "number"
        ? output.verified_source_count
        : undefined,
    replacement_candidates_used:
      typeof output.replacement_candidates_used === "number"
        ? output.replacement_candidates_used
        : undefined,
    source_shortfall:
      typeof output.source_shortfall === "number"
        ? output.source_shortfall
        : undefined,
  };
};

function SearchTracePanel({
  trace,
  onRestart,
  onResume,
  restartBusy,
  isAdmin,
}: {
  trace: SearchRunTrace;
  onRestart: () => void;
  onResume: () => void;
  restartBusy: boolean;
  isAdmin: boolean;
}) {
  const hasRunningStage = trace.agent_runs.some(
    (stage) => stage.status !== "completed" && stage.status !== "failed",
  );
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!hasRunningStage) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasRunningStage]);

  // Пока поиск идёт, ход — главное на экране. Как только он закончился,
  // главным становится результат, а этапы сворачиваются в одну строку:
  // иначе к найденным поставщикам приходится прокручивать весь журнал.
  //
  // Завершённость берётся из статуса прогона, а не из «нет работающих
  // этапов»: у задачи в очереди этапов нет вообще, и по второму признаку
  // она сворачивалась бы как законченная.
  const finished = FINISHED_SEARCH_STATUSES.includes(trace.status);
  const [collapsed, setCollapsed] = useState(finished);
  const wasUnfinished = useRef(!finished);
  useEffect(() => {
    if (finished && wasUnfinished.current) setCollapsed(true);
    wasUnfinished.current = !finished;
  }, [finished]);

  const finishedSteps = trace.agent_runs.filter(
    (stage) => stage.status === "completed",
  ).length;
  const failedSteps = trace.agent_runs.filter(
    (stage) => stage.status === "failed",
  ).length;

  if (collapsed) {
    return (
      <section className="search-trace search-trace-collapsed">
        <div className="search-trace-header">
          <div className="heading-with-help">
            <h2>Ход поиска</h2>
            <span className={`badge ${traceTone(trace.status)}`}>
              {SEARCH_STATUS_LABELS[trace.status] || trace.status}
            </span>
            <span className="note">
              этапов пройдено: {finishedSteps}
              {failedSteps > 0 ? ` · с ошибкой: ${failedSteps}` : ""}
            </span>
          </div>
          <button
            className="secondary"
            type="button"
            onClick={() => setCollapsed(false)}
          >
            Показать этапы
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="search-trace">
      <div className="search-trace-header">
        <div>
          <div className="heading-with-help">
            <h2>Ход поиска</h2>
            <HelpTip text="Этапы выполняются последовательно. Завершённые отмечены зелёным, текущий — синим, ещё не запущенные — серым." />
          </div>
          {(() => {
            const spent = totalTokens(trace.agent_runs);
            if (!spent.input && !spent.output) return null;
            return (
              <p className="note">
                Токены запуска: <strong>{formatTokens(spent.input)}</strong> на
                вход, <strong>{formatTokens(spent.output)}</strong> на выход.
                Вход обычно дороже выхода в несколько раз, поэтому расход
                определяется тем, сколько текста страниц уходит модели.
              </p>
            );
          })()}
        </div>
        <div className="search-trace-actions">
          <span className={`badge ${traceTone(trace.status)}`}>
            {SEARCH_STATUS_LABELS[trace.status] || trace.status}
          </span>
          {finished && (
            <button
              className="secondary"
              type="button"
              onClick={() => setCollapsed(true)}
            >
              Свернуть
            </button>
          )}
          {trace.can_resume && (
            <button
              className="secondary"
              disabled={restartBusy}
              onClick={onResume}
              type="button"
              title="Повторить только проверку страниц, оценку и аудит: найденные кандидаты сохраняются, веб-поиск не выполняется заново."
            >
              <Icon name="refresh" size={16} />
              {restartBusy ? "Продолжение…" : "Продолжить с проверки"}
            </button>
          )}
          {trace.can_restart && (
            <button
              className="secondary"
              disabled={restartBusy}
              onClick={onRestart}
              type="button"
            >
              <Icon name="refresh" size={16} />
              {restartBusy ? "Перезапуск…" : "Перезапустить задачу"}
            </button>
          )}
        </div>
      </div>

      {trace.error && (
        <div className="qualification-warning">
          {userErrorMessage(trace.error)}
        </div>
      )}
      {trace.is_stale && (
        <div className="qualification-warning">
          Задача не передавала прогресс более 30 минут. Её можно безопасно
          перезапустить: текущая трассировка сохранится в истории, а новый
          запуск продолжит поиск новых поставщиков.
        </div>
      )}

      <div className="agent-pipeline">
        {PIPELINE_STEPS.map((step, index) => {
          const stage = [...trace.agent_runs].reverse().find(
            (item) => item.agent_slug === step.slug,
          );
          const skipped =
            !stage &&
            index >= 4 &&
            (trace.status === "completed" || trace.status === "search_completed") &&
            trace.summary.candidate_count === 0;
          const state = skipped
            ? "skipped"
            : !stage
              ? "waiting"
            : stage.status === "completed"
              ? "completed"
              : stage.status === "failed"
                ? "failed"
                : "running";
          const statusLabel =
            state === "completed"
              ? "Готово"
              : state === "running"
                ? "Выполняется"
                : state === "failed"
                  ? "Ошибка"
                  : state === "skipped"
                    ? "Пропущено: кандидаты не найдены"
                    : "Ожидает запуска";
          // Пока шаг идёт, показываем последнее действие агента вместо общего
          // описания: пользователю важно видеть, чем он занят прямо сейчас.
          const lastEvent = stage?.events?.length
            ? stage.events[stage.events.length - 1]
            : null;
          const content = (
            <>
              <span className={`pipeline-marker ${state}`}>
                {state === "completed" ? "✓" : index + 1}
              </span>
              <span className="pipeline-copy">
                <strong>{step.title}</strong>
                {state === "running" && lastEvent ? (
                  <small className="pipeline-activity">
                    {lastEvent.message}
                    <span className="thinking-dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                  </small>
                ) : (
                  <small>{step.description}</small>
                )}
              </span>
              <span className={`pipeline-status ${state}`}>
                {state === "running" && (
                  <span className="loading-spinner" aria-hidden="true" />
                )}
                {statusLabel}
              </span>
            </>
          );
          if (!stage) {
            return (
              <div className={`agent-pipeline-step ${state}`} key={step.slug}>
                {content}
              </div>
            );
          }
          const elapsedMs =
            stage.latency_ms ??
            (state === "running"
              ? Math.max(0, now - new Date(stage.started_at).getTime())
              : null);
          return (
            <details
              className={`agent-pipeline-step ${state}`}
              key={`${step.slug}-${stage.id}`}
              open={state === "failed" || undefined}
            >
              <summary>{content}</summary>
              <div className="agent-step-details">
                <div className="agent-trace-meta">
                  {elapsedMs !== null && (
                    <span>
                      Время: {formatDuration(elapsedMs)}
                      {state === "running" && elapsedMs >= 30 * 60 * 1000
                        ? " · нет прогресса более 30 минут"
                        : ""}
                    </span>
                  )}
                  {(stage.prompt_tokens ?? stage.completion_tokens) !== null && (
                    <span title="Расход токенов этапа. Вход обычно вчетверо больше выхода, поэтому экономия сидит в том, что отправляется модели.">
                      Токены: {formatTokens(stage.prompt_tokens)} вход ·{" "}
                      {formatTokens(stage.completion_tokens)} выход
                    </span>
                  )}
                </div>
                {stage.error && (
                  <div className="qualification-warning stage-error-block">
                    <span>{userErrorMessage(stage.error)}</span>
                    {state === "failed" && trace.can_resume && (
                      <button
                        className="secondary stage-error-action"
                        disabled={restartBusy}
                        onClick={onResume}
                        type="button"
                        title="Повторить проверку страниц, оценку и аудит: найденные кандидаты сохраняются, веб-поиск не выполняется заново."
                      >
                        <Icon name="refresh" size={14} />
                        {restartBusy
                          ? "Перезапуск…"
                          : "Перезапустить с этого шага"}
                      </button>
                    )}
                    {state === "failed" &&
                      !trace.can_resume &&
                      trace.can_restart && (
                        <button
                          className="secondary stage-error-action"
                          disabled={restartBusy}
                          onClick={onRestart}
                          type="button"
                          title="Поиск не успел сохранить результаты, поэтому задача перезапускается целиком. Ранее найденные поставщики исключаются из повторного поиска."
                        >
                          <Icon name="refresh" size={14} />
                          {restartBusy ? "Перезапуск…" : "Перезапустить задачу"}
                        </button>
                      )}
                  </div>
                )}
                {stage.events && stage.events.length > 0 && (
                  <section className="agent-event-log">
                    <h3>Журнал агента</h3>
                    <ol className="agent-event-list">
                      {stage.events.map((event, eventIndex) => (
                        <li
                          className={`agent-event ${event.kind}`}
                          key={`${stage.id}-event-${eventIndex}`}
                        >
                          <span className="agent-event-time">
                            {new Date(event.at).toLocaleTimeString("ru-RU")}
                          </span>
                          <span className="agent-event-message">
                            {event.message}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </section>
                )}
                {stage.output_payload && (
                  <section className="stage-result">
                    <h3>Результат этапа</h3>
                    <StageResult
                      slug={step.slug}
                      output={stage.output_payload}
                    />
                  </section>
                )}
              </div>
            </details>
          );
        })}
      </div>

      {isAdmin && (
      <details className="technical-accordion">
        <summary>
          Технические детали
          <span>
            {trace.summary.executed_query_count} запросов ·{" "}
            {trace.summary.raw_page_count} страниц
          </span>
        </summary>
        <div className="technical-accordion-body">
          <div className="agent-trace-meta">
            <span>Correlation ID: {trace.correlation_id}</span>
            <span>Граф: {trace.graph_version}</span>
          </div>
          <section>
            <h3>Этапы: промпты, входные данные и артефакты решений</h3>
            {trace.agent_runs.map((stage) => (
              <details className="trace-subdetails" key={`tech-${stage.id}`}>
                <summary>
                  {stage.sequence}. {stage.agent_name}
                  <span className="trace-subdetails-meta">
                    {stage.execution_type === "llm"
                      ? "ИИ-агент"
                      : stage.execution_type === "tool"
                        ? "поисковый инструмент"
                        : "резервный алгоритм"}
                    {stage.prompt_version !== null
                      ? ` · промпт в. ${stage.prompt_version}`
                      : ""}
                    {` · контракт ${stage.contract_version}`}
                  </span>
                </summary>
                <div className="trace-subdetails-body">
                  {stage.effective_system_prompt && (
                    <details className="trace-subdetails">
                      <summary>Промпт ИИ-агента</summary>
                      <div className="trace-block">
                        <pre>{stage.effective_system_prompt}</pre>
                      </div>
                    </details>
                  )}
                  {stage.input_payload && (
                    <details className="trace-subdetails">
                      <summary>Входные данные</summary>
                      <div className="trace-block">
                        <pre>{formatJson(stage.input_payload)}</pre>
                      </div>
                    </details>
                  )}
                  {stage.output_payload && (
                    <details className="trace-subdetails">
                      <summary>Совместимый итоговый результат</summary>
                      <div className="trace-block">
                        <pre>{formatJson(stage.output_payload)}</pre>
                      </div>
                    </details>
                  )}
                  {stage.raw_output_payload && (
                    <details className="trace-subdetails">
                      <summary>Сырой ответ модели</summary>
                      <div className="trace-block">
                        <pre>{formatJson(stage.raw_output_payload)}</pre>
                      </div>
                    </details>
                  )}
                  {stage.parsed_output_payload && (
                    <details className="trace-subdetails">
                      <summary>Результат typed parsing</summary>
                      <div className="trace-block">
                        <pre>{formatJson(stage.parsed_output_payload)}</pre>
                      </div>
                    </details>
                  )}
                  {stage.validation_output_payload && (
                    <details className="trace-subdetails">
                      <summary>Результат валидаторов</summary>
                      <div className="trace-block">
                        <pre>{formatJson(stage.validation_output_payload)}</pre>
                      </div>
                    </details>
                  )}
                  {stage.policy_output_payload && (
                    <details className="trace-subdetails">
                      <summary>Итог policy gate</summary>
                      <div className="trace-block">
                        <pre>{formatJson(stage.policy_output_payload)}</pre>
                      </div>
                    </details>
                  )}
                </div>
              </details>
            ))}
          </section>
          <section>
            <h3>Источники и параметры поисковых запросов</h3>
            {trace.search_attempts.length === 0 && (
              <p className="note">Поисковые инструменты ещё не запускались.</p>
            )}
            <div className="search-attempts">
              {trace.search_attempts.map((attempt) => (
                <details className="search-attempt-card" key={attempt.id}>
                  <summary>
                    <span className={`badge ${traceTone(attempt.status)}`}>
                      {attempt.status === "completed" ? "готово" : attempt.status}
                    </span>
                    <strong>{attempt.query}</strong>
                    <span className="note">
                      результатов: {attempt.result_count ?? "—"}
                    </span>
                  </summary>
                  {attempt.purpose && (
                    <p className="note">
                      Цель: {PURPOSE_LABELS[attempt.purpose] || attempt.purpose}
                    </p>
                  )}
                  {attempt.error && (
                    <div className="qualification-warning">
                      {userErrorMessage(attempt.error)}
                    </div>
                  )}
                  {attempt.results_payload?.map((result) => (
                    <div
                      className="search-attempt-result"
                      key={`${attempt.id}-${result.url}`}
                    >
                      <a href={result.url} target="_blank" rel="noreferrer">
                        {result.title}
                      </a>
                      <p>{result.snippet}</p>
                    </div>
                  ))}
                </details>
              ))}
            </div>
          </section>

          <section>
            <h3>Сохранённые источники</h3>
            {trace.source_documents.length === 0 && (
              <p className="note">Первичные страницы ещё не загружались.</p>
            )}
            <div className="source-documents">
              {trace.source_documents.map((source) => (
                <details className="source-document-card" key={source.id}>
                  <summary>
                    <span className={`badge ${traceTone(source.status)}`}>
                      {source.status}
                    </span>
                    <strong>{source.title || source.domain || source.url}</strong>
                  </summary>
                  <a
                    href={source.final_url || source.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {source.final_url || source.url}
                  </a>
                  {source.error && (
                    <div className="qualification-warning">
                      {userErrorMessage(
                        source.error,
                        "Источник не удалось проверить. Система продолжит поиск по другим источникам.",
                      )}
                    </div>
                  )}
                </details>
              ))}
            </div>
          </section>

          <section>
            <h3>Подтверждения</h3>
            {trace.evidence_claims.length === 0 && (
              <p className="note">Проверенных цитат пока нет.</p>
            )}
            <div className="evidence-claims">
              {trace.evidence_claims.map((claim) => {
                const source = trace.source_documents.find(
                  (item) => item.id === claim.source_document_id,
                );
                return (
                  <article className="evidence-claim-card" key={claim.id}>
                    <div>
                      <span
                        className={`badge ${
                          claim.support_status === "supports"
                            ? "tone-ok"
                            : "tone-danger"
                        }`}
                      >
                        {claim.support_status === "supports"
                          ? "подтверждает"
                          : "противоречит"}
                      </span>
                      <strong>
                        {CLAIM_LABELS[claim.claim_type] || claim.claim_type}
                      </strong>
                    </div>
                    <blockquote>«{claim.quote}»</blockquote>
                    {source && (
                      <a
                        href={source.final_url || source.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Открыть источник
                      </a>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        </div>
      </details>
      )}
    </section>
  );
}

export default function SupplierSearchSection({
  rfq,
  onOpenSubstance,
}: {
  rfq: RFQRead;
  onOpenSubstance?: (id: number) => void;
}) {
  const { user } = useAuth();
  // Решения принимает человек с правом записи: аудитор смотрит.
  const canDecide = user?.role !== "auditor";
  const supportedRfqCountries = (rfq.search_countries ?? []).filter((country) =>
    COUNTRY_OPTIONS.includes(country),
  );
  const [selectedCountries, setSelectedCountries] = useState<string[]>(
    supportedRfqCountries.length ? supportedRfqCountries : ["Китай"],
  );
  const [searchMode, setSearchMode] = useState<SearchModeKey>(
    modeFromCompanies(rfq.supplier_target),
  );
  const [searchScope, setSearchScope] = useState<SearchScope>("manufacturers");
  const [instructions, setInstructions] = useState("");
  const [repeatSearchOpen, setRepeatSearchOpen] = useState(false);
  const [data, setData] = useState<SupplierSearchResponse | null>(null);
  const [qualification, setQualification] = useState<SupplierQualificationResponse | null>(null);
  // URL остаётся уникальным после объединения запусков одной страны. В отличие
  // от result_index он не начинается заново в каждом поиске.
  const [detailUrl, setDetailUrl] = useState<string | null>(null);

  // Окно закрывается клавишей, а не только кнопкой: закупщик просматривает
  // строки подряд и не тянется к мыши ради каждой.
  useEffect(() => {
    if (detailUrl === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailUrl(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailUrl]);
  const [trace, setTrace] = useState<SearchRunTrace | null>(null);
  const [runs, setRuns] = useState<SearchRunListItem[]>([]);
  // Пока история запусков не пришла, вкладка не знает, был ли поиск вообще.
  // Хранится не флагом, а номером загруженного запроса: при переходе к
  // другому запросу ожидание начинается заново само.
  const [loadedRfqId, setLoadedRfqId] = useState<number | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [restartBusy, setRestartBusy] = useState(false);
  // Показывает загрузку только при явном переключении запуска: сама
  // трассировка обновляется опросом каждые три секунды без действий
  // пользователя.
  const [traceBusy, setTraceBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Реестр компаний: из него берутся решения человека по строкам таблицы.
  const [registry, setRegistry] = useState<SupplierRead[]>([]);
  const [busySupplierId, setBusySupplierId] = useState<number | null>(null);
  const [substanceRecord, setSubstanceRecord] = useState<SubstanceRecord | null>(null);
  const [identityDecision, setIdentityDecision] = useState<"reject" | null>(null);
  const [correctedName, setCorrectedName] = useState(rfq.name);
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionBusy, setDecisionBusy] = useState(false);

  useEffect(() => {
    let active = true;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const items = await api.listSearchRuns(50, rfq.id);
        if (!active) return;
        setRuns(items);
        const selected = items.find((item) => item.id === selectedRunId);
        const selectedCountry = selected ? runText(selected, "country") : "";
        const latestForCountry = selectedCountry
          ? items.find((item) => runText(item, "country") === selectedCountry)
          : items[0];
        const traceId = latestForCountry?.id ?? null;
        if (traceId !== selectedRunId) {
          setSelectedRunId(traceId);
        }
        if (traceId !== null) {
          const currentTrace = await api.getSearchRun(traceId);
          if (!active) return;
          setTrace(currentTrace);
          setData(currentTrace.result_payload);
          const restoredQualification = qualificationFromTrace(currentTrace);
          setQualification(restoredQualification);
        }
      } catch {
        // A transient polling failure must not hide the form or current result.
      } finally {
        refreshing = false;
        if (active) setLoadedRfqId(rfq.id);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [rfq.id, selectedRunId]);

  const search = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    setQualification(null);
    setTrace(null);
    setData(null);
    try {
      const jobs = [];
      for (const selectedCountry of selectedCountries) {
        jobs.push(
          await api.enqueueSupplierSearch(rfq.id, {
            cas: rfq.cas,
            name: rfq.name,
            country: selectedCountry,
            additional_instructions: instructions || null,
            limit: modeCompanies(searchMode),
            search_scope: searchScope,
          }),
        );
      }
      const job = jobs[jobs.length - 1];
      setSelectedRunId(job.search_run_id);
      setTrace(await api.getSearchRun(job.search_run_id));
      setNotice(
        `Добавлено задач: ${jobs.length}. Агент откроет и проверит до ${modeCompanies(searchMode)} компаний в каждой стране.`,
      );
      setRepeatSearchOpen(false);
      setRuns(await api.listSearchRuns(50, rfq.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Что искать: изготовителей или всех продавцов. Второе нужно, когда
  // задача — сравнить цену среди доступных продавцов, а не найти завод.
  const toggleSearchCountry = (country: string) =>
    setSelectedCountries((current) =>
      current.includes(country)
        ? current.filter((item) => item !== country)
        : [...current, country],
    );

  const openRun = async (runId: number) => {
    setSelectedRunId(runId);
    setQualification(null);
    setData(null);
    setError(null);
    setTraceBusy(true);
    try {
      const currentTrace = await api.getSearchRun(runId);
      setTrace(currentTrace);
      setData(currentTrace.result_payload);
      setQualification(qualificationFromTrace(currentTrace));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setTraceBusy(false);
    }
  };

  const initialLoading = loadedRfqId !== rfq.id;
  const activeCas = trace ? runText(trace, "cas") : rfq.cas;
  const activeName = trace ? runText(trace, "name") : rfq.name;
  const activeCountry = trace
    ? runText(trace, "country")
    : selectedCountries[0] ?? "";
  const candidateResults = trace?.candidate_results ?? data?.results ?? [];
  // Реестр нужен только там, где есть таблица находок.
  useEffect(() => {
    if (!qualification) return;
    let cancelled = false;
    void api
      .listSuppliers()
      .then((items) => {
        if (!cancelled) setRegistry(items);
      })
      .catch(() => {
        // Молча: без реестра таблица работает, просто без решений.
        if (!cancelled) setRegistry([]);
      });
    return () => {
      cancelled = true;
    };
  }, [qualification]);

  const registrySupplierFor = (
    result: SupplierQualificationResponse["results"][number],
  ) => {
    const storedSource = result.url.slice(0, 255);
    const bySource = registry.find((item) => item.source === storedSource);
    if (bySource) return bySource;
    const key = companyKey(result.company_name);
    return key
      ? registry.find((item) => companyKey(item.company) === key)
      : undefined;
  };

  const savedToRegistry = (
    result: SupplierQualificationResponse["results"][number],
  ) => registrySupplierFor(result) !== undefined;

  // Решение по компании живёт в реестре, а не в сохранённом прогоне:
  // прогон — снимок находки, а подтвердить или вычеркнуть компанию можно
  // и через неделю после него. Поэтому статусы берутся живыми из реестра
  // и связываются со строкой по URL или тому же нормализованному имени,
  // которое backend использует при дедупликации. result_index не подходит:
  // он повторяется в объединённых запусках одной страны.
  const decisionFor = (
    result: SupplierQualificationResponse["results"][number],
  ): SupplierDecision | null => {
    const supplier = registrySupplierFor(result);
    if (supplier === undefined) return null;
    return {
      supplier_id: supplier.id,
      qualification_status: supplier.qualification_status,
      excluded_here: (supplier.linked_requests ?? []).some(
        (link) => link.rfq_id === rfq.id && link.excluded,
      ),
    };
  };

  const decide = async (
    decision: SupplierDecision,
    action: DecisionAction,
  ) => {
    if (busySupplierId !== null) return;
    // Исключение из реестра закрывает компанию во всех запросах, включая
    // чужие. Спрашиваем — отменить это можно только вручную и потом.
    if (
      action === "rejected" &&
      !window.confirm(
        "Исключить компанию из реестра? Она перестанет предлагаться во всех " +
          "запросах, не только в этом.",
      )
    ) {
      return;
    }
    setBusySupplierId(decision.supplier_id);
    setError(null);
    try {
      if (action === "exclude_here" || action === "return_here") {
        await api.setSupplierExclusion(
          rfq.id,
          decision.supplier_id,
          action === "exclude_here",
        );
      } else {
        await api.setSupplierQualification(decision.supplier_id, action);
      }
      setRegistry(await api.listSuppliers());
      setNotice(DECISION_NOTICES[action]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusySupplierId(null);
    }
  };
  const detailResult =
    qualification?.results.find(
      (result) => result.url === detailUrl,
    ) ?? null;
  const countryRuns = useMemo(() => {
    const seen = new Set<string>();
    return runs.filter((run) => {
      const country = runText(run, "country") || "Без страны";
      if (seen.has(country)) return false;
      seen.add(country);
      return true;
    });
  }, [runs]);
  const activeRun =
    countryRuns.find((run) => run.id === selectedRunId) ?? countryRuns[0];
  const restartTrace = async () => {
    if (!trace) return;
    setRestartBusy(true);
    setError(null);
    setNotice(null);
    try {
      const job = await api.restartSearchRun(trace.id);
      setSelectedRunId(job.search_run_id);
      setTrace(await api.getSearchRun(job.search_run_id));
      setRuns(await api.listSearchRuns(50, rfq.id));
      setNotice(
        "Задача перезапущена. Ранее найденные поставщики сохранены и исключены из нового поиска.",
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setRestartBusy(false);
    }
  };

  const resumeTrace = async () => {
    if (!trace) return;
    setRestartBusy(true);
    setError(null);
    setNotice(null);
    try {
      const job = await api.resumeSearchRun(trace.id);
      setSelectedRunId(job.search_run_id);
      setTrace(await api.getSearchRun(job.search_run_id));
      setRuns(await api.listSearchRuns(50, rfq.id));
      setNotice(
        "Задача продолжена с шага проверки: найденные кандидаты сохранены, " +
          "веб-поиск повторяться не будет. Worker подхватит задачу, как " +
          "только локальная ИИ-модель будет готова.",
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setRestartBusy(false);
    }
  };

  const saveIdentityDecision = async (action: "confirm" | "reject") => {
    if (!data) return;
    const suggestedName = data.identity.canonical_name || activeName;
    setDecisionBusy(true);
    setError(null);
    try {
      const saved = await api.decideSubstanceIdentity(rfq.id, {
        action,
        suggested_name: suggestedName,
        preferred_name:
          action === "confirm" ? activeName : correctedName.trim() || null,
        synonyms:
          action === "confirm"
            ? [
                activeName,
                suggestedName,
                ...data.identity.search_names,
              ]
            : [activeName],
        note: decisionNote.trim() || null,
        verification: data.substance_lookup,
      });
      setSubstanceRecord(saved);
      setIdentityDecision(null);
      setNotice(
        action === "confirm"
          ? "Соответствие подтверждено. Названия сохранены в справочнике веществ."
          : "Вывод ИИ отклонён. Исправленное название и исключение сохранены.",
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setDecisionBusy(false);
    }
  };

  return (
    <div className="supplier-search-workspace">
      <div className="tab-toolbar">
        <div className="heading-with-help">
          <h1>Поиск компаний</h1>
          <HelpTip text="ИИ-агент ищет компании по выбранным странам, затем помогает проверить их роль, документы и соответствие веществу." />
        </div>
      </div>

      <div className="panel search-overview-panel">
        <div className="search-overview-header">
          <div className="search-overview-title-row">
            <div className="heading-with-help">
              <h2>Текущий поиск</h2>
              <HelpTip text="Выберите страну, чтобы увидеть статус и результаты соответствующей задачи поиска." />
            </div>
            <button
              aria-controls="repeat-search-settings"
              aria-expanded={repeatSearchOpen}
              className="secondary repeat-search-toggle"
              type="button"
              onClick={() => setRepeatSearchOpen((current) => !current)}
            >
              <Icon name="search" size={16} />
              Повторный поиск с другими параметрами
            </button>
          </div>
          {runs.length > 0 && (
            <label className="current-search-field">
              <span className="ui-field-label">Страна поиска</span>
              <div className="current-search-controls">
                <Select
                  ariaLabel="Выбор текущего поиска"
                  value={activeRun ? String(activeRun.id) : ""}
                  onChange={(next) => void openRun(Number(next))}
                  options={countryRuns.map((run) => ({
                    value: String(run.id),
                    label: runText(run, "country") || "Без страны",
                  }))}
                />
                {traceBusy && (
                  <span className="current-search-refresh">
                    <span className="loading-spinner" aria-hidden="true" />
                    Загрузка
                  </span>
                )}
              </div>
            </label>
          )}
        </div>
        {initialLoading ? (
          // Пустая вкладка молчала, пока шли два запроса подряд, и успевала
          // соврать «поиск ещё не запускался» у запроса с запусками.
          <p className="note current-search-loading">
            <span className="loading-spinner" aria-hidden="true" />
            Загружаем историю поиска…
          </p>
        ) : runs.length === 0 ? (
          <p className="note">Поиск ещё не запускался.</p>
        ) : (
          activeRun && (
            <div className="current-search-summary">
              <span className={`badge ${traceTone(activeRun.status)}`}>
                {SEARCH_STATUS_LABELS[activeRun.status] || activeRun.status}
              </span>
              {activeRun.queue_position !== null && (
                <span>Позиция в очереди: {activeRun.queue_position}</span>
              )}
              {trace && trace.merged_run_count > 1 && (
                <span>
                  Объединены уникальные результаты запусков:{" "}
                  {trace.merged_run_count}
                </span>
              )}
            </div>
          )
        )}
        {repeatSearchOpen && (
          <section
            aria-label="Настроить повторный поиск"
            className="repeat-search-settings"
            id="repeat-search-settings"
          >
            <div className="repeat-search-settings-header">
              <div>
                <h3>Настроить повторный поиск</h3>
                <p>
                  Измените страны, количество поставщиков или дополнительные
                  требования для нового запуска.
                </p>
              </div>
              <button
                aria-label="Закрыть настройки повторного поиска"
                className="ui-icon-button"
                type="button"
                onClick={() => setRepeatSearchOpen(false)}
              >
                <Icon name="close" size={17} />
              </button>
            </div>
            <div className="repeat-search-settings-body">
              <div className="field">
                <label>Что искать</label>
                <div className="checks">
                  <label>
                    <input
                      type="radio"
                      name="search-scope"
                      checked={searchScope === "manufacturers"}
                      onChange={() => setSearchScope("manufacturers")}
                    />
                    Только производителей
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="search-scope"
                      checked={searchScope === "all_sellers"}
                      onChange={() => setSearchScope("all_sellers")}
                    />
                    Всех продавцов
                  </label>
                </div>
                <span className="muted">
                  {searchScope === "manufacturers"
                    ? "Торговые площадки и каталоги из раздела «Посредники» отсеиваются до загрузки страниц."
                    : "Площадки не отсеиваются: режим для сравнения цен среди доступных продавцов."}
                </span>
              </div>
              <div className="field">
                <label>Страны поиска</label>
                <div className="checks repeat-search-countries">
                  {COUNTRY_OPTIONS.map((country) => (
                    <label key={country}>
                      <input
                        checked={selectedCountries.includes(country)}
                        type="checkbox"
                        onChange={() => toggleSearchCountry(country)}
                      />
                      {country}
                    </label>
                  ))}
                </div>
                {selectedCountries.length === 0 && (
                  <span className="error">Выберите хотя бы одну страну.</span>
                )}
              </div>
              <div className="field">
                <div className="heading-with-help">
                  <label>Насколько тщательно искать</label>
                  <HelpTip text="Режим задаёт, сколько компаний агент откроет и проверит в каждой стране. Это объём проверки, а не обещание результата: производителем оказывается не всякая проверенная компания. Число поисковых запросов режим не меняет." />
                </div>
                <div className="search-modes">
                  {SEARCH_MODES.map((mode) => (
                    <label
                      key={mode.key}
                      className={`search-mode${searchMode === mode.key ? " active" : ""}`}
                    >
                      <input
                        type="radio"
                        name="search-mode-rerun"
                        value={mode.key}
                        checked={searchMode === mode.key}
                        onChange={() => setSearchMode(mode.key)}
                      />
                      <span className="search-mode-label">{mode.label}</span>
                      <span className="search-mode-hint">{mode.hint}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="field">
                <label>Дополнительные требования</label>
                <Textarea
                  rows={3}
                  maxLength={4000}
                  placeholder="Например: только производители фармацевтического грейда с GMP"
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                />
              </div>
              <button
                disabled={busy || selectedCountries.length === 0}
                type="button"
                onClick={() => void search()}
              >
                {busy
                  ? "Добавляем задачи…"
                  : `Начать поиск в ${selectedCountries.length} стран${
                      selectedCountries.length === 1 ? "е" : "ах"
                    }`}
              </button>
            </div>
          </section>
        )}
      </div>

      {notice && <Toast message={notice} onClose={() => setNotice(null)} />}
      {error && <p className="error">{error}</p>}
      {trace && (
        <div className="panel">
          <SearchTracePanel
            trace={trace}
            onRestart={() => void restartTrace()}
            onResume={() => void resumeTrace()}
            restartBusy={restartBusy}
            isAdmin={user?.role === "admin"}
          />
        </div>
      )}
      {data && (
        <div className="panel search-explain">
          <div className="heading-with-help">
            <h2>Что и как искали</h2>
            <HelpTip text="Как ИИ-агент понял вещество и какие запросы составил. Отсюда видно, почему выдача получилась именно такой." />
          </div>
          <div className="qualification-evidence">
            <span className="badge tone-ok">
              Стратегия: {SEARCH_STRATEGY_LABELS[data.search_strategy]}
            </span>
            {Object.entries(data.source_counts).map(([source, count]) => {
              const kind = source as SupplierSearchResult["source_kind"];
              return (
                <span className={`badge ${sourceTone(kind)}`} key={source}>
                  {SOURCE_LABELS[kind]}: {count}
                </span>
              );
            })}
          </div>
          <details className="content-accordion">
            <summary>Результат идентификации вещества ИИ-агентом</summary>
            <div className="content-accordion-body search-identity">
              <strong>{data.identity.canonical_name || activeName}</strong>
              <div className="note">
                {activeCas
                  ? `CAS: ${activeCas} · справочник: ${
                      data.substance_lookup.found
                        ? "подтверждён"
                        : "не подтверждён"
                    }`
                  : "CAS не указан · поиск по названию, эталону и спецификации"}
                {data.substance_lookup.molecular_formula
                  ? ` · ${data.substance_lookup.molecular_formula}`
                  : ""}
              </div>
              {data.identity.search_names.length > 0 && (
                <div className="note">
                  Также искали: {data.identity.search_names.join(", ")}
                </div>
              )}
              {data.identity.ambiguities.map((item) => (
                <div className="qualification-warning" key={item}>
                  Требует внимания: {item}
                </div>
              ))}
              {activeCas && <div className="identity-decision">
                <div>
                  <strong>Решение специалиста</strong>
                  <p className="note">
                    Подтвердите соответствие или укажите корректное название.
                    Решение сохранится для следующих запросов.
                  </p>
                </div>
                {(substanceRecord || rfq.substance_id) && (
                  <div className="identity-saved-rule">
                    <Icon name="check" size={17} />
                    <span>
                      {substanceRecord?.review_status === "confirmed" ||
                      rfq.substance_review_status === "confirmed"
                        ? "Правило подтверждено и сохранено"
                        : "Карточка вещества сохранена и требует уточнения"}
                    </span>
                    {onOpenSubstance && (
                      <button
                        className="secondary"
                        onClick={() =>
                          onOpenSubstance(
                            substanceRecord?.id ?? rfq.substance_id!,
                          )
                        }
                      >
                        Открыть карточку
                      </button>
                    )}
                  </div>
                )}
                {user?.role !== "auditor" && (
                  <div className="identity-actions">
                    <button
                      disabled={decisionBusy}
                      onClick={() => void saveIdentityDecision("confirm")}
                    >
                      Подтвердить соответствие
                    </button>
                    <button
                      className="secondary"
                      disabled={decisionBusy}
                      onClick={() =>
                        setIdentityDecision((current) =>
                          current === "reject" ? null : "reject",
                        )
                      }
                    >
                      Указать другое название
                    </button>
                  </div>
                )}
                {identityDecision === "reject" && (
                  <div className="identity-correction-form">
                    <label>
                      <span className="ui-field-label">Корректное наименование</span>
                      <Input
                        value={correctedName}
                        onChange={(event) => setCorrectedName(event.target.value)}
                      />
                    </label>
                    <label>
                      <span className="ui-field-label">Комментарий к решению</span>
                      <Textarea
                        rows={2}
                        placeholder="Почему предложенное название не подходит"
                        value={decisionNote}
                        onChange={(event) => setDecisionNote(event.target.value)}
                      />
                    </label>
                    <button
                      disabled={decisionBusy || !correctedName.trim()}
                      onClick={() => void saveIdentityDecision("reject")}
                    >
                      {decisionBusy ? "Сохранение…" : "Сохранить исправление"}
                    </button>
                  </div>
                )}
              </div>}
            </div>
          </details>
          <details className="content-accordion">
            <summary>
              Поисковая стратегия и запросы ({data.search_plan.length})
            </summary>
            <div className="content-accordion-body search-plan-list">
              {data.search_plan.map((item) => (
                <article key={item.query}>
                  <strong>
                    {PURPOSE_LABELS[item.purpose] || "Поисковый запрос"}
                  </strong>
                  <code>{item.query}</code>
                  <small>
                    Язык: {LANGUAGE_LABELS[item.language] || item.language}
                  </small>
                </article>
              ))}
            </div>
          </details>
        </div>
      )}
      {(data || candidateResults.length > 0) && (
        <div className="panel">
          <div className="search-results-header">
            <div className="heading-with-help">
              <h2>Найденные компании</h2>
              <HelpTip text="Кандидаты, найденные по этому запросу, от большего балла к меньшему. Заголовки сортируют, строка открывает подробности. Проверка предварительная: перед решением откройте первичные источники и запросите документы у поставщика." />
            </div>
          </div>
          {!data && (
            <div className="qualification-warning">
              {trace && trace.merged_run_count > 1
                ? "Новый запуск ещё выполняется. Ниже сохранены объединённые результаты предыдущих поисков по этой стране."
                : "Это результат старого запуска. Итоговый пакет не был сохранён, поэтому кандидаты восстановлены из журнала поискового инструмента."}
            </div>
          )}
          {candidateResults.length === 0 && (
            <p className="note">Поисковый источник не вернул результатов. Попробуйте убрать часть дополнительных требований.</p>
          )}
          {!qualification && candidateResults.length > 0 && (
            <details className="content-accordion" open>
              <summary>Кандидаты из поисковой выдачи ({candidateResults.length})</summary>
              <div className="content-accordion-body candidate-list">
                {candidateResults.map((result) => (
                  <details className="candidate-row" key={result.url}>
                    <summary>
                      <a
                        href={result.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {result.title}
                      </a>
                      <span className={`badge ${sourceTone(result.source_kind)}`}>
                        {SOURCE_LABELS[result.source_kind]}
                      </span>
                      <span
                        className={`badge ${
                          result.country_hint === "likely"
                            ? "tone-ok"
                            : "tone-neutral"
                        }`}
                      >
                        {result.country_hint === "likely"
                          ? "Страна совпадает"
                          : "Страна не подтверждена"}
                      </span>
                    </summary>
                    <div className="candidate-row-body">
                      <p>{result.snippet}</p>
                      <a href={result.url} target="_blank" rel="noreferrer">
                        Открыть источник
                      </a>
                    </div>
                  </details>
                ))}
              </div>
            </details>
          )}
          {qualification && (
            <>
              {/* Два представления вместо одного списка: закупщику — короткий
                  безопасный список, эксперту — всё найденное. Раньше они шли
                  вперемешку, и принадлежность к короткому списку отличалась
                  только цветом одного тега из десяти. */}
              <QualificationTable
                results={qualification.results.filter((item) => !item.winnowed)}
                activeCas={activeCas}
                activeCountry={activeCountry}
                onSelect={(result) => setDetailUrl(result.url)}
                decisionFor={decisionFor}
                onDecide={(decision, action) => void decide(decision, action)}
                busySupplierId={busySupplierId}
                canDecide={canDecide}
              />
              {qualification.results.some((item) => item.winnowed) && (
                // Прячем только то, чего в списке компаний не будет.
                // Свернуть заодно и находки с чужих страниц не вышло: они
                // в список идут, и получалось, что компания есть в
                // «Отобранных», а в «Найденных» её надо разворачивать.
                // Именно такое расхождение и привело к задаче про #37.
                <details className="content-accordion winnowed-results">
                  <summary>
                    Отсеянные:{" "}
                    {qualification.results.filter((item) => item.winnowed).length}
                  </summary>
                  <div className="content-accordion-body">
                    <p className="note">
                      Страницы, на которых компания не названа: рейтинги,
                      подборки, страницы с ошибкой загрузки. В списке компаний
                      их нет — писать по такой находке некому. Всё остальное
                      найденное стоит в таблице выше, а находки не с сайта
                      компании помечены там «с чужой страницы».
                    </p>
                    <QualificationTable
                      results={qualification.results.filter(
                        (item) => item.winnowed,
                      )}
                      activeCas={activeCas}
                      activeCountry={activeCountry}
                      onSelect={(result) => setDetailUrl(result.url)}
                      decisionFor={decisionFor}
                      onDecide={(decision, action) => void decide(decision, action)}
                      busySupplierId={busySupplierId}
                      canDecide={canDecide}
                    />
                  </div>
                </details>
              )}
              {detailResult && (
                <div
                  className="request-delete-backdrop"
                  role="presentation"
                  onClick={() => setDetailUrl(null)}
                >
                  <section
                    aria-labelledby="qualification-detail-title"
                    aria-modal="true"
                    className="supplier-detail qualification-detail"
                    role="dialog"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <header>
                      <h2 id="qualification-detail-title">
                        {detailResult.company_name}
                      </h2>
                      <button
                        className="secondary"
                        type="button"
                        onClick={() => setDetailUrl(null)}
                      >
                        Закрыть
                      </button>
                    </header>
                    <QualificationDetail
                      result={detailResult}
                      activeCas={activeCas}
                      activeCountry={activeCountry}
                      trace={trace}
                      savedToRegistry={savedToRegistry(detailResult)}
                    />
                  </section>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
