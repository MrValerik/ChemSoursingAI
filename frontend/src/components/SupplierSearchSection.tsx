import { useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  CasEvidenceStatus,
  CountryEvidenceStatus,
  EvidenceStatus,
  QualifiedSupplierResult,
  QualifiedSupplierType,
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

const SOURCE_LABELS: Record<SupplierSearchResult["source_kind"], string> = {
  echemi: "Echemi",
  india_registry: "Официальный источник Индии",
  india_web: "Индийский сайт",
  web: "Открытый веб",
};

const sourceTone = (source: SupplierSearchResult["source_kind"]) => {
  if (source === "echemi") return "tone-ok";
  if (source === "india_registry") return "tone-ok";
  if (source === "india_web") return "tone-warn";
  return "tone-neutral";
};

const evidenceTone = (status: EvidenceStatus) => {
  if (status === "claimed") return "tone-warn";
  if (status === "contradicted") return "tone-danger";
  return "tone-neutral";
};

export default function SupplierSearchSection() {
  const { user } = useAuth();
  const [cas, setCas] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("Китай");
  const [instructions, setInstructions] = useState("");
  const [data, setData] = useState<SupplierSearchResponse | null>(null);
  const [qualification, setQualification] = useState<SupplierQualificationResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [qualifying, setQualifying] = useState(false);
  const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    setBusy(true);
    setError(null);
    setQualification(null);
    setAddedUrls(new Set());
    try {
      setData(
        await api.searchSuppliers({
          cas,
          name,
          country: country || null,
          additional_instructions: instructions || null,
        }),
      );
    } catch (e) {
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
      setQualification(
        await api.qualifySuppliers({
          cas,
          name,
          country: country || null,
          additional_instructions: instructions || null,
          results: data.results.slice(0, 5),
        }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setQualifying(false);
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
          ? `ИИ-уверенность ${qualified.confidence}%, требуется проверка`
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
          <p className="note">
            Сначала проверяются карточки Echemi, затем сайты и реестры выбранной страны.
            Qwen формирует запрос и квалифицирует найденные свидетельства.
          </p>
        </div>
      </div>
      <div className="panel">
        <div className="row">
          <div className="field"><label>CAS</label><input value={cas} onChange={(e) => setCas(e.target.value)} /></div>
          <div className="field" style={{ flex: 2 }}><label>Вещество</label><input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="field">
            <label>Страна</label>
            <input
              list="supplier-country-options"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
            />
            <datalist id="supplier-country-options">
              <option value="Китай" />
              <option value="Индия" />
            </datalist>
          </div>
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
          <div className="note">
            Стратегия: Echemi в первую очередь · Qwen: {data.ai_used ? "да" : "fallback"}
          </div>
          <div className="note">Основной запрос: {data.query}</div>
          <div className="qualification-evidence">
            {Object.entries(data.source_counts).map(([source, count]) => (
              <span className={`badge ${sourceTone(source as SupplierSearchResult["source_kind"])}`} key={source}>
                {SOURCE_LABELS[source as SupplierSearchResult["source_kind"]]}: {count}
              </span>
            ))}
          </div>
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
                    <span className={`badge ${sourceTone(result.source_kind)}`}>
                      {SOURCE_LABELS[result.source_kind]}
                    </span>
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
                        <span className={`badge ${sourceTone(result.source_kind)}`}>
                          {SOURCE_LABELS[result.source_kind]}
                        </span>
                      </div>
                      <div className="confidence">
                        <strong>{result.confidence}%</strong>
                        <span>уверенность</span>
                      </div>
                    </div>

                    <h4>{result.title_ru}</h4>
                    <p>{result.summary_ru}</p>

                    <div className="qualification-evidence">
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
    </div>
  );
}
