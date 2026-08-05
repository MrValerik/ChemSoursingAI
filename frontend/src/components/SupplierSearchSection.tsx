import { useEffect, useMemo, useState } from "react";
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
  SupplierSearchResponse,
  SupplierSearchResult,
  SupplierVerificationStatus,
  SubstanceRecord,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
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

const VERIFICATION_LABELS: Record<SupplierVerificationStatus, string> = {
  confirmed: "Аудитор подтвердил",
  needs_review: "Нужна ручная проверка",
  rejected: "Аудитор отклонил",
  unavailable: "Аудитор недоступен",
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

const evidenceTone = (status: EvidenceStatus) => {
  if (status === "claimed") return "tone-warn";
  if (status === "contradicted") return "tone-danger";
  return "tone-neutral";
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
    "Для включения в короткий список нужны одновременно:",
    `балл не ниже 70 (${result.confidence >= 70 ? "выполнено" : `сейчас ${result.confidence}`});`,
    `тип «Производитель» (${result.supplier_type === "manufacturer" ? "выполнено" : "не подтверждено"});`,
    `подтверждение вещества (${hasIdentity ? "есть" : "нет"});`,
    `подтверждение собственного производства (${hasManufacturerRole ? "есть" : "нет"});`,
    `решение независимого аудитора (${result.verification?.status === "confirmed" ? "подтверждено" : "не подтверждено"}).`,
  ].join(" ");
};

const verificationExplanation = (
  result: SupplierQualificationResponse["results"][number],
) => {
  if (!result.verification) {
    return (
      "Для этого сохранённого результата независимая проверка не выполнялась. " +
      "Кандидат требует ручной проверки."
    );
  }
  return [
    result.verification.reason,
    result.verification.gate_reason,
    `Уверенность аудитора: ${result.verification.confidence}%.`,
  ].join(" ");
};

const formatJson = (value: unknown) => JSON.stringify(value, null, 2);

const traceTone = (status: string) => {
  if (status === "completed" || status === "search_completed") return "tone-ok";
  if (status === "failed") return "tone-danger";
  return "tone-warn";
};

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
    title: "Независимый аудит",
    description: "Повторно сверяет вещество и роль производителя перед коротким списком.",
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
                <span>{PURPOSE_LABELS[displayText(query.purpose, "")] || "Поиск поставщиков"}</span>
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
    warning:
      "Квалификация предварительная. Проверьте первичные источники и документы перед решением.",
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
  const supportedRfqCountries = (rfq.search_countries ?? []).filter((country) =>
    COUNTRY_OPTIONS.includes(country),
  );
  const [selectedCountries, setSelectedCountries] = useState<string[]>(
    supportedRfqCountries.length ? supportedRfqCountries : ["Китай"],
  );
  const [supplierTarget, setSupplierTarget] = useState(rfq.supplier_target ?? 5);
  const [searchScope, setSearchScope] = useState<SearchScope>("manufacturers");
  const [instructions, setInstructions] = useState("");
  const [repeatSearchOpen, setRepeatSearchOpen] = useState(false);
  const [data, setData] = useState<SupplierSearchResponse | null>(null);
  const [qualification, setQualification] = useState<SupplierQualificationResponse | null>(null);
  const [trace, setTrace] = useState<SearchRunTrace | null>(null);
  const [runs, setRuns] = useState<SearchRunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [restartBusy, setRestartBusy] = useState(false);
  // Показывает загрузку только при явном переключении запуска: сама
  // трассировка обновляется опросом каждые три секунды без действий
  // пользователя.
  const [traceBusy, setTraceBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
            limit: supplierTarget,
            search_scope: searchScope,
          }),
        );
      }
      const job = jobs[jobs.length - 1];
      setSelectedRunId(job.search_run_id);
      setTrace(await api.getSearchRun(job.search_run_id));
      setNotice(
        `Добавлено задач: ${jobs.length}. ИИ-агент будет искать до ${supplierTarget} поставщиков в каждой стране.`,
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

  const activeCas = trace ? runText(trace, "cas") : rfq.cas;
  const activeName = trace ? runText(trace, "name") : rfq.name;
  const activeCountry = trace
    ? runText(trace, "country")
    : selectedCountries[0] ?? "";
  const candidateResults = trace?.candidate_results ?? data?.results ?? [];
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
          <h1>Поиск поставщиков</h1>
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
        {runs.length === 0 ? (
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
              <div className="field compact-field">
                <label>Поставщиков в каждой стране</label>
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={supplierTarget}
                  onChange={(event) =>
                    setSupplierTarget(
                      Math.min(20, Math.max(1, Number(event.target.value) || 1)),
                    )
                  }
                />
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
      {(data || candidateResults.length > 0) && (
        <div className="panel">
          <div className="search-results-header">
            <div className="heading-with-help">
              <h2>Найденные поставщики</h2>
              <HelpTip text="Сначала показываются кандидаты из поисковой выдачи. Дополнительная проверка помогает определить производителей, документы и риски." />
            </div>
          </div>
          {data ? (
            <>
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
            </>
          ) : (
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
              <div className="qualification-warning">
                Проверка предварительная. Перед решением откройте источники и
                запросите документы у поставщика.
              </div>
              <div className="qualification-grid">
                {qualification.results.map((result) => (
                  <article className="qualification-card" key={result.url}>
                    <div className="qualification-card-header">
                      <div>
                        <h3>{result.company_name}</h3>
                        <span className={`badge ${result.supplier_type === "manufacturer" ? "tone-ok" : result.supplier_type === "distributor" ? "tone-warn" : "tone-neutral"}`}>
                          {TYPE_LABELS[result.supplier_type]}
                        </span>
                        <span className={`badge ${sourceTone(result.source_kind)}`}>
                          {SOURCE_LABELS[result.source_kind]}
                        </span>
                      </div>
                      <div className="confidence">
                        <div className="confidence-value">
                          <strong>{result.confidence}%</strong>
                          <HelpTip text={scoreExplanation(result)} />
                        </div>
                        <span>балл по проверенным данным</span>
                      </div>
                    </div>

                    <h4>{result.title_ru}</h4>
                    <p>{result.summary_ru}</p>

                    <div className="qualification-evidence">
                      <EvidenceBadge
                        className={result.shortlist_eligible ? "tone-ok" : "tone-neutral"}
                        label={
                          result.shortlist_eligible
                            ? "Включён в короткий список"
                            : "Не включён в короткий список"
                        }
                        explanation={shortlistExplanation(result)}
                      />
                      <EvidenceBadge
                        className={verificationTone(
                          result.verification?.status ?? "unavailable",
                        )}
                        label={
                          VERIFICATION_LABELS[
                            result.verification?.status ?? "unavailable"
                          ]
                        }
                        explanation={verificationExplanation(result)}
                      />
                      <EvidenceBadge
                        className={
                          result.cas_status === "confirmed"
                            ? "tone-ok"
                            : result.cas_status === "mismatch"
                              ? "tone-danger"
                              : "tone-neutral"
                        }
                        label={`${activeCas ? "CAS" : "Идентичность"}: ${CAS_LABELS[result.cas_status]}`}
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
                      {DOCUMENT_FIELDS.map((document) => {
                        const status = result[document.key];
                        return (
                          <EvidenceBadge
                            className={evidenceTone(status)}
                            explanation={evidenceExplanation(
                              result,
                              document.key.replace("_status", ""),
                              `Статус «${EVIDENCE_LABELS[status]}» означает результат проверки упоминания ${document.label} на первичной странице. Он не заменяет проверку самого документа.`,
                            )}
                            key={document.key}
                            label={`${document.label}: ${EVIDENCE_LABELS[status]}`}
                          />
                        );
                      })}
                    </div>

                    <details className="score-breakdown">
                      <summary>Показать расчёт балла</summary>
                      <ul>
                        <li>Совпадение вещества: {result.score_breakdown.identity}/35</li>
                        <li>Роль компании: {result.score_breakdown.supplier_role}/25</li>
                        <li>Страна: {result.score_breakdown.country}/10</li>
                        <li>Документы: {result.score_breakdown.documents}/15</li>
                        <li>Качество доказательств: {result.score_breakdown.evidence_quality}/15</li>
                      </ul>
                      {result.llm_confidence !== null && (
                        <p className="note">
                          Исходная оценка ИИ-агента: {result.llm_confidence}% —
                          показана для аудита, но не участвует в итоговом балле.
                        </p>
                      )}
                    </details>

                    {result.red_flags.length > 0 && (
                      <div className="qualification-warning">
                        <strong>Риски:</strong> {result.red_flags.join("; ")}
                      </div>
                    )}
                    {result.missing_evidence.length > 0 && (
                      <div className="note">
                        <strong>Запросить:</strong> {result.missing_evidence.join("; ")}
                      </div>
                    )}

                    {result.evidence.length > 0 && (
                      <details className="candidate-evidence">
                        <summary>
                          Проверенные цитаты ({result.evidence.length})
                        </summary>
                        <div className="candidate-evidence-list">
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
                      </details>
                    )}

                    <details>
                      <summary>Исходный фрагмент поисковой выдачи</summary>
                      <p className="note">
                        Заголовок и краткое описание получены от поисковой
                        системы до проверки страницы. Они сохранены для аудита
                        поиска и не считаются доказательством.
                      </p>
                      <p className="note">{result.title}</p>
                      <p className="note">{result.snippet}</p>
                    </details>
                    <a className="source-link" href={result.url} target="_blank" rel="noreferrer">
                      Открыть первичный источник
                    </a>

                    {qualification.registry_links?.some(
                      (link) => link.result_index === result.result_index,
                    ) && (
                      <div className="candidate-auto-saved">
                        <Icon name="check" size={16} />
                        Кандидат автоматически сохранён в реестре поставщиков
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </>
          )}
        </div>
      )}
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
    </div>
  );
}
