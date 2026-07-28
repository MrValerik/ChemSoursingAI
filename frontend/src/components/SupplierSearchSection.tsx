import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  CasEvidenceStatus,
  CountryEvidenceStatus,
  EvidenceStatus,
  QualifiedSupplierResult,
  QualifiedSupplierType,
  RFQRead,
  SearchRunListItem,
  SearchRunTrace,
  SupplierQualificationResponse,
  SupplierSearchResponse,
  SupplierSearchResult,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";

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

const DOCUMENT_FIELDS = [
  { key: "gmp_status", label: "GMP" },
  { key: "iso_status", label: "ISO" },
  { key: "coa_status", label: "CoA" },
  { key: "tds_status", label: "TDS" },
] as const;

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
  completed: "Завершено",
  failed: "Ошибка",
  cancelled: "Отменено",
};

const COUNTRY_OPTIONS = [
  "Китай",
  "Индия",
  "Турция",
  "Германия",
  "США",
];

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

function HelpTip({ text }: { text: string }) {
  return (
    <span className="help-tip">
      <button type="button" aria-label={text}>
        ?
      </button>
      <span role="tooltip">{text}</span>
    </span>
  );
}

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
  const stage = [...trace.agent_runs]
    .reverse()
    .find((item) => item.agent_slug === "supplier_qualification");
  return {
    search_run_id: trace.id,
    results: trace.qualified_results,
    prompt_id: stage?.prompt_id ?? null,
    prompt_version: stage?.prompt_version ?? null,
    warning:
      "Квалификация предварительная. Проверьте первичные источники и документы перед решением.",
  };
};

function SearchTracePanel({
  trace,
  busy,
  onRefresh,
}: {
  trace: SearchRunTrace;
  busy: boolean;
  onRefresh: () => void;
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
        </div>
        <div className="search-trace-actions">
          <span className={`badge ${traceTone(trace.status)}`}>
            {SEARCH_STATUS_LABELS[trace.status] || trace.status}
          </span>
          <button className="secondary" disabled={busy} onClick={onRefresh}>
            {busy ? "Обновление…" : "Обновить"}
          </button>
        </div>
      </div>

      {trace.error && <div className="qualification-warning">{trace.error}</div>}

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
          const content = (
            <>
              <span className={`pipeline-marker ${state}`}>
                {state === "completed" ? "✓" : index + 1}
              </span>
              <span className="pipeline-copy">
                <strong>{step.title}</strong>
                <small>{step.description}</small>
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
            >
              <summary>{content}</summary>
              <div className="agent-step-details">
                <div className="agent-trace-meta">
                  <span>
                    Исполнитель:{" "}
                    {stage.execution_type === "llm"
                      ? "ИИ-агент"
                      : stage.execution_type === "tool"
                        ? "поисковый инструмент"
                        : "резервный алгоритм"}
                  </span>
                  {stage.prompt_version !== null && (
                    <span>Промпт: версия {stage.prompt_version}</span>
                  )}
                  {elapsedMs !== null && (
                    <span>Время: {formatDuration(elapsedMs)}</span>
                  )}
                </div>
                {stage.error && (
                  <div className="qualification-warning">{stage.error}</div>
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
                    <summary>Технические данные результата</summary>
                    <div className="trace-block">
                      <pre>{formatJson(stage.output_payload)}</pre>
                    </div>
                  </details>
                )}
              </div>
            </details>
          );
        })}
      </div>

      <details className="technical-accordion">
        <summary>
          Технические детали
          <span>
            {trace.summary.executed_query_count} запросов ·{" "}
            {trace.summary.raw_page_count} страниц
          </span>
        </summary>
        <div className="technical-accordion-body">
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
                    <div className="qualification-warning">{attempt.error}</div>
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
                    <div className="qualification-warning">{source.error}</div>
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
    </section>
  );
}

export default function SupplierSearchSection({ rfq }: { rfq: RFQRead }) {
  const { user } = useAuth();
  const [selectedCountries, setSelectedCountries] = useState<string[]>(
    rfq.search_countries?.length ? rfq.search_countries : ["Китай"],
  );
  const [countryToAdd, setCountryToAdd] = useState("");
  const [supplierTarget, setSupplierTarget] = useState(rfq.supplier_target ?? 5);
  const [instructions, setInstructions] = useState("");
  const [data, setData] = useState<SupplierSearchResponse | null>(null);
  const [qualification, setQualification] = useState<SupplierQualificationResponse | null>(null);
  const [trace, setTrace] = useState<SearchRunTrace | null>(null);
  const [runs, setRuns] = useState<SearchRunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [traceBusy, setTraceBusy] = useState(false);
  const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
        if (selectedRunId === null && items.length > 0) {
          setSelectedRunId(items[0].id);
        }
        if (selectedRunId !== null) {
          const currentTrace = await api.getSearchRun(selectedRunId);
          if (!active) return;
          setTrace(currentTrace);
          if (currentTrace.result_payload) {
            setData(currentTrace.result_payload);
          }
          const restoredQualification = qualificationFromTrace(currentTrace);
          if (restoredQualification) setQualification(restoredQualification);
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
    setAddedUrls(new Set());
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
          }),
        );
      }
      const job = jobs[jobs.length - 1];
      setSelectedRunId(job.search_run_id);
      setTrace(await api.getSearchRun(job.search_run_id));
      setNotice(
        `Добавлено задач: ${jobs.length}. ИИ-агент будет искать до ${supplierTarget} поставщиков в каждой стране.`,
      );
      setRuns(await api.listSearchRuns(50, rfq.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const addCountry = () => {
    if (!countryToAdd || selectedCountries.includes(countryToAdd)) return;
    setSelectedCountries((current) => [...current, countryToAdd]);
    setCountryToAdd("");
  };

  const removeCountry = (value: string) => {
    setSelectedCountries((current) =>
      current.length === 1 ? current : current.filter((item) => item !== value),
    );
  };

  const openRun = async (runId: number) => {
    setSelectedRunId(runId);
    setQualification(null);
    setData(null);
    setAddedUrls(new Set());
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
  const candidateResults = data?.results ?? trace?.candidate_results ?? [];
  const activeRun = runs.find((run) => run.id === selectedRunId) ?? runs[0];
  const availableCountries = [
    ...new Set([
      ...COUNTRY_OPTIONS,
      ...(rfq.search_countries ?? []),
      ...runs.map((run) => runText(run, "country")).filter(Boolean),
    ]),
  ];

  const refreshTrace = async () => {
    const runId = selectedRunId ?? qualification?.search_run_id ?? data?.search_run_id;
    if (!runId) return;
    setTraceBusy(true);
    setError(null);
    try {
      setTrace(await api.getSearchRun(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setTraceBusy(false);
    }
  };

  const add = async (result: SupplierSearchResult | QualifiedSupplierResult) => {
    try {
      const qualified = "company_name" in result ? result : null;
      await api.addSupplier(
        {
          company: (qualified?.company_name || result.title).slice(0, 255),
          type:
            qualified && qualified.supplier_type !== "unknown"
              ? qualified.supplier_type
              : null,
          country: activeCountry || null,
          source: result.url,
          reputation: qualified
            ? `Проверяемый балл ${qualified.confidence}/100, требуется решение человека`
            : null,
          qualification_status: "candidate",
          evidence_score: qualified?.confidence ?? null,
        },
        rfq.id,
        trace?.id ?? data?.search_run_id,
      );
      setAddedUrls((current) => new Set(current).add(result.url));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
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
          <div>
            <div className="heading-with-help">
              <h2>Текущий поиск</h2>
              <HelpTip text="Выберите страну, чтобы увидеть статус и результаты соответствующей задачи поиска." />
            </div>
          </div>
          <div className="current-search-controls">
            {runs.length > 0 && (
              <select
                aria-label="Выбор текущего поиска"
                value={activeRun?.id ?? ""}
                onChange={(event) => void openRun(Number(event.target.value))}
              >
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {runText(run, "country") || "Без страны"} —{" "}
                    {SEARCH_STATUS_LABELS[run.status] || run.status}
                  </option>
                ))}
              </select>
            )}
            {traceBusy && (
              <span className="current-search-refresh">
                <span className="loading-spinner" aria-hidden="true" />
                Обновление
              </span>
            )}
          </div>
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
            </div>
          )
        )}
      </div>

      <details className="panel settings-accordion">
        <summary>
          <span>
            <strong>Настройки и новый поиск</strong>
            <small>Страны, количество поставщиков и дополнительные требования</small>
          </span>
        </summary>
        <div className="settings-accordion-body">
          <div className="field">
            <label>Страны поиска</label>
            <div className="country-tokens">
              {selectedCountries.map((selectedCountry) => (
                <span className="country-token" key={selectedCountry}>
                  {selectedCountry}
                  <button
                    type="button"
                    aria-label={`Убрать страну ${selectedCountry}`}
                    disabled={selectedCountries.length === 1}
                    onClick={() => removeCountry(selectedCountry)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div className="country-picker">
              <select
                value={countryToAdd}
                onChange={(event) => setCountryToAdd(event.target.value)}
              >
                <option value="">Выберите страну</option>
                {availableCountries
                  .filter((item) => !selectedCountries.includes(item))
                  .map((item) => (
                    <option value={item} key={item}>
                      {item}
                    </option>
                  ))}
              </select>
              <button
                className="secondary"
                type="button"
                disabled={!countryToAdd}
                onClick={addCountry}
              >
                Добавить
              </button>
            </div>
          </div>
          <div className="field compact-field">
            <label>Поставщиков в каждой стране</label>
            <input
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
            <textarea
              rows={3}
              maxLength={4000}
              placeholder="Например: только производители фармацевтического грейда с GMP"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
            />
          </div>
          <button
            disabled={busy || selectedCountries.length === 0}
            onClick={() => void search()}
          >
            {busy
              ? "Добавляем задачи…"
              : `Начать поиск в ${selectedCountries.length} стран${
                  selectedCountries.length === 1 ? "е" : "ах"
                }`}
          </button>
        </div>
      </details>

      {notice && <p className="success">{notice}</p>}
      {error && <p className="error">{error}</p>}
      {(data || candidateResults.length > 0) && (
        <div className="panel">
          <div className="search-results-header">
            <div className="heading-with-help">
              <h2>Найденные поставщики</h2>
              <HelpTip text="Сначала показываются кандидаты из поисковой выдачи. Дополнительная проверка помогает определить производителей, документы и риски." />
            </div>
            <strong>{candidateResults.length}</strong>
          </div>
          {data ? (
            <>
              <details className="content-accordion">
                <summary>Результат идентификации вещества ИИ-агентом</summary>
                <div className="content-accordion-body search-identity">
                  <strong>{data.identity.canonical_name || activeName}</strong>
                  <div className="note">
                    CAS: {activeCas} · справочник:{" "}
                    {data.substance_lookup.found ? "подтверждён" : "не подтверждён"}
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
              Это результат старого запуска. Итоговый пакет не был сохранён,
              поэтому кандидаты восстановлены из журнала поискового инструмента.
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
                      </div>
                      <div className="confidence">
                        <strong>{result.confidence}%</strong>
                        <span>проверяемый балл</span>
                      </div>
                    </div>

                    <h4>{result.title_ru}</h4>
                    <p>{result.summary_ru}</p>

                    <div className="qualification-evidence">
                      <span className={`badge ${result.shortlist_eligible ? "tone-ok" : "tone-neutral"}`}>
                        {result.shortlist_eligible ? "Допущен в shortlist" : "Только экспертный список"}
                      </span>
                      <span className={`badge ${result.cas_status === "confirmed" ? "tone-ok" : result.cas_status === "mismatch" ? "tone-danger" : "tone-neutral"}`}>
                        CAS: {CAS_LABELS[result.cas_status]}
                      </span>
                      <span className={`badge ${result.country_status === "claimed" ? "tone-ok" : result.country_status === "mismatch" ? "tone-danger" : result.country_status === "likely" ? "tone-warn" : "tone-neutral"}`}>
                        {activeCountry}: {COUNTRY_LABELS[result.country_status]}
                      </span>
                      {DOCUMENT_FIELDS.map((document) => {
                        const status = result[document.key];
                        return (
                          <span className={`badge ${evidenceTone(status)}`} key={document.key}>
                            {document.label}: {EVIDENCE_LABELS[status]}
                          </span>
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
                      <div className="candidate-evidence">
                        <strong>Проверенные цитаты:</strong>
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

                    <details>
                      <summary>Показать оригинальный текст</summary>
                      <p className="note">{result.title}</p>
                      <p className="note">{result.snippet}</p>
                    </details>
                    <a className="source-link" href={result.url} target="_blank" rel="noreferrer">
                      Открыть первичный источник
                    </a>

                    {user?.role !== "auditor" && (
                      <button
                        className="secondary"
                        disabled={addedUrls.has(result.url)}
                        onClick={() => void add(result)}
                      >
                        {addedUrls.has(result.url) ? "Кандидат добавлен" : "Добавить кандидата"}
                      </button>
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
            busy={traceBusy}
            onRefresh={() => void refreshTrace()}
          />
        </div>
      )}
    </div>
  );
}
