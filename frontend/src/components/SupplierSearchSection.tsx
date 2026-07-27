import { useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  CasEvidenceStatus,
  CountryEvidenceStatus,
  EvidenceStatus,
  QualifiedSupplierResult,
  QualifiedSupplierType,
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

function SearchTracePanel({
  trace,
  busy,
  onRefresh,
}: {
  trace: SearchRunTrace;
  busy: boolean;
  onRefresh: () => void;
}) {
  return (
    <section className="search-trace">
      <div className="search-trace-header">
        <div>
          <h2>Ход поиска и работа агентов</h2>
          <p className="note">
            Запуск #{trace.id} · {trace.owner_name || `пользователь ${trace.owner_id}`}
          </p>
        </div>
        <div className="search-trace-actions">
          <span className={`badge ${traceTone(trace.status)}`}>{trace.status}</span>
          <button className="secondary" disabled={busy} onClick={onRefresh}>
            {busy ? "Обновление…" : "Обновить журнал"}
          </button>
        </div>
      </div>

      {trace.error && <div className="qualification-warning">{trace.error}</div>}

      <div className="agent-trace-list">
        {trace.agent_runs.map((stage) => (
          <details className="agent-trace-card" key={stage.id}>
            <summary>
              <span className="agent-trace-order">{stage.sequence}</span>
              <span>
                <strong>{stage.agent_name}</strong>
                <small>
                  {stage.execution_type === "llm"
                    ? "Локальная Qwen"
                    : stage.execution_type === "tool"
                      ? "Системный инструмент, без промпта"
                      : "Детерминированный fallback"}
                </small>
              </span>
              <span className={`badge ${traceTone(stage.status)}`}>{stage.status}</span>
              <span className="note">
                {stage.latency_ms === null ? "—" : `${stage.latency_ms} мс`}
              </span>
            </summary>

            <div className="agent-trace-meta">
              <span>Код: {stage.agent_slug}</span>
              {stage.prompt_version !== null && (
                <span>Промпт: #{stage.prompt_id}, версия {stage.prompt_version}</span>
              )}
              {stage.model && <span>Модель: {stage.model}</span>}
              {stage.temperature !== null && <span>temperature: {stage.temperature}</span>}
              {stage.max_tokens !== null && <span>max tokens: {stage.max_tokens}</span>}
            </div>

            {stage.error && <div className="qualification-warning">{stage.error}</div>}

            {stage.effective_system_prompt && (
              <div className="trace-block">
                <h3>Фактический системный промпт</h3>
                <pre>{stage.effective_system_prompt}</pre>
              </div>
            )}
            {stage.input_payload && (
              <div className="trace-block">
                <h3>Вход агента</h3>
                <pre>{formatJson(stage.input_payload)}</pre>
              </div>
            )}
            {stage.output_payload && (
              <div className="trace-block">
                <h3>Результат агента</h3>
                <pre>{formatJson(stage.output_payload)}</pre>
              </div>
            )}
          </details>
        ))}
      </div>

      <div className="search-attempts">
        <h3>Где и какими запросами искали</h3>
        {trace.search_attempts.length === 0 && (
          <p className="note">Поисковые инструменты в этом запуске не вызывались.</p>
        )}
        {trace.search_attempts.map((attempt) => (
          <details className="search-attempt-card" key={attempt.id}>
            <summary>
              <span className={`badge ${traceTone(attempt.status)}`}>{attempt.status}</span>
              <strong>{attempt.connector}</strong>
              <span className="note">
                {attempt.language || "язык не указан"} · результатов: {attempt.result_count ?? "—"}
              </span>
            </summary>
            <div className="search-query-text">{attempt.query}</div>
            {attempt.purpose && <p className="note">Цель: {attempt.purpose}</p>}
            {attempt.error && <div className="qualification-warning">{attempt.error}</div>}
            {attempt.results_payload?.map((result) => (
              <div className="search-attempt-result" key={`${attempt.id}-${result.url}`}>
                <a href={result.url} target="_blank" rel="noreferrer">{result.title}</a>
                <p>{result.snippet}</p>
                <span>{result.url}</span>
              </div>
            ))}
          </details>
        ))}
      </div>

      <div className="source-documents">
        <h3>Снимки первичных страниц</h3>
        {trace.source_documents.length === 0 && (
          <p className="note">Первичные страницы в этом запуске ещё не загружались.</p>
        )}
        {trace.source_documents.map((source) => (
          <details className="source-document-card" key={source.id}>
            <summary>
              <span className={`badge ${traceTone(source.status)}`}>{source.status}</span>
              <strong>{source.title || source.domain || source.url}</strong>
              <span className="note">
                HTTP {source.http_status ?? "—"} · {source.content_type || "тип не определён"}
              </span>
            </summary>
            <a href={source.final_url || source.url} target="_blank" rel="noreferrer">
              {source.final_url || source.url}
            </a>
            {source.content_hash && (
              <p className="source-hash">SHA-256: {source.content_hash}</p>
            )}
            {source.error && <div className="qualification-warning">{source.error}</div>}
            {source.text_content && (
              <div className="trace-block">
                <h3>Текст, переданный агенту</h3>
                <pre>{source.text_content}</pre>
              </div>
            )}
          </details>
        ))}
      </div>

      <div className="evidence-claims">
        <h3>Проверенные атомарные доказательства</h3>
        {trace.evidence_claims.length === 0 && (
          <p className="note">
            Агент пока не вернул ни одной цитаты, прошедшей серверную проверку.
          </p>
        )}
        {trace.evidence_claims.map((claim) => {
          const source = trace.source_documents.find(
            (item) => item.id === claim.source_document_id,
          );
          return (
            <article className="evidence-claim-card" key={claim.id}>
              <div>
                <span
                  className={`badge ${
                    claim.support_status === "supports" ? "tone-ok" : "tone-danger"
                  }`}
                >
                  {claim.support_status === "supports" ? "подтверждает" : "противоречит"}
                </span>
                <strong>{CLAIM_LABELS[claim.claim_type] || claim.claim_type}</strong>
              </div>
              <p>{claim.claim_value}</p>
              <blockquote>«{claim.quote}»</blockquote>
              {source && (
                <a href={source.final_url || source.url} target="_blank" rel="noreferrer">
                  Источник #{source.id}: {source.title || source.domain || source.url}
                </a>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export default function SupplierSearchSection() {
  const { user } = useAuth();
  const [cas, setCas] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("China");
  const [instructions, setInstructions] = useState("");
  const [data, setData] = useState<SupplierSearchResponse | null>(null);
  const [qualification, setQualification] = useState<SupplierQualificationResponse | null>(null);
  const [trace, setTrace] = useState<SearchRunTrace | null>(null);
  const [busy, setBusy] = useState(false);
  const [qualifying, setQualifying] = useState(false);
  const [traceBusy, setTraceBusy] = useState(false);
  const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    setBusy(true);
    setError(null);
    setQualification(null);
    setTrace(null);
    setAddedUrls(new Set());
    try {
      const result = await api.searchSuppliers({
        cas,
        name,
        country: country || null,
        additional_instructions: instructions || null,
      });
      setData(result);
      setTrace(await api.getSearchRun(result.search_run_id));
    } catch (e) {
      if (e instanceof ApiError && e.searchRunId) {
        try {
          setTrace(await api.getSearchRun(e.searchRunId));
        } catch {
          // The original search error is more useful than a secondary trace error.
        }
      }
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const qualify = async () => {
    if (!data) return;
    setQualifying(true);
    setError(null);
    try {
      const result = await api.qualifySuppliers({
        search_run_id: data.search_run_id,
        cas,
        name,
        country: country || null,
        additional_instructions: instructions || null,
        results: data.results.slice(0, 5),
      });
      setQualification(result);
      setTrace(await api.getSearchRun(result.search_run_id));
    } catch (e) {
      if (e instanceof ApiError && e.searchRunId) {
        try {
          setTrace(await api.getSearchRun(e.searchRunId));
        } catch {
          // Keep the qualification error if the trace cannot be reloaded.
        }
      }
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setQualifying(false);
    }
  };

  const refreshTrace = async () => {
    const runId = qualification?.search_run_id ?? data?.search_run_id;
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
      await api.addSupplier({
        company: (qualified?.company_name || result.title).slice(0, 255),
        type:
          qualified && qualified.supplier_type !== "unknown"
            ? qualified.supplier_type
            : null,
        country: country || null,
        source: result.url,
        reputation: qualified
          ? `Проверяемый балл ${qualified.confidence}/100, требуется решение человека`
          : null,
      });
      setAddedUrls((current) => new Set(current).add(result.url));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className="requests-page">
      <div className="requests-header">
        <div>
          <h1>Поиск поставщиков</h1>
          <p className="note">Qwen формирует запрос, а найденные факты сохраняются со ссылками.</p>
        </div>
      </div>
      <div className="panel">
        <div className="row">
          <div className="field"><label>CAS</label><input value={cas} onChange={(e) => setCas(e.target.value)} /></div>
          <div className="field" style={{ flex: 2 }}><label>Вещество</label><input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="field"><label>Страна</label><input value={country} onChange={(e) => setCountry(e.target.value)} /></div>
        </div>
        <div className="field">
          <label>Дополнительный поисковый промпт</label>
          <textarea
            rows={3}
            maxLength={4000}
            placeholder="Например: только производители фармацевтического грейда с GMP"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>
        <button disabled={busy || !cas.trim() || !name.trim()} onClick={() => void search()}>
          {busy ? "Qwen и поиск работают…" : "Найти поставщиков"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <div className="panel">
          <div className="note">Основной запрос: {data.query} · Qwen: {data.ai_used ? "да" : "fallback"}</div>
          {data.fallback_used && (
            <p className="note">
              Для полноты выполнено несколько запросов, включая локализованные по стране.
            </p>
          )}
          <details className="search-queries">
            <summary>Показать использованные запросы ({data.queries_used.length})</summary>
            <ul>
              {data.queries_used.map((query) => <li key={query}>{query}</li>)}
            </ul>
          </details>
          <p className="note">{data.warning}</p>
          {data.results.length === 0 && (
            <p className="note">Поисковый источник не вернул результатов. Попробуйте убрать часть дополнительных требований.</p>
          )}
          {data.results.length > 0 && !qualification && (
            <div className="qualification-action">
              <button disabled={qualifying} onClick={() => void qualify()}>
                {qualifying ? "Qwen переводит и квалифицирует…" : "Перевести и квалифицировать результаты"}
              </button>
              <span className="note">Обычно занимает 1–3 минуты на Tesla T4.</span>
            </div>
          )}
          {!qualification &&
            data.results.map((result) => (
              <div className="rfq-list-item" key={result.url}>
                <div style={{ flex: 1 }}>
                  <a href={result.url} target="_blank" rel="noreferrer">{result.title}</a>
                  <div>
                    <span className={`badge ${result.country_hint === "likely" ? "tone-ok" : "tone-neutral"}`}>
                      {result.country_hint === "likely" ? "Есть признаки нужной страны" : "Страна требует проверки"}
                    </span>
                  </div>
                  <div className="note">{result.snippet}</div>
                  <div className="cas">{result.url}</div>
                </div>
              </div>
            ))}
          {qualification && (
            <>
              <p className="note">{qualification.warning}</p>
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
                        {country}: {COUNTRY_LABELS[result.country_status]}
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
                          Исходная оценка Qwen: {result.llm_confidence}% — показана для аудита,
                          но не участвует в итоговом балле.
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
