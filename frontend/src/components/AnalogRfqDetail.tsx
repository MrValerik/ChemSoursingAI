// Карточка запроса на подбор аналога.
//
// Это не обычный запрос с урезанными вкладками, а другая задача. У обычного
// запроса есть вещество, которое закупают: по нему ищут компании, ведут
// переписку и сводят котировки. Здесь вещества ещё нет — есть позиция, для
// которой ищут замену. Поэтому «Отобранные компании», «Общение» и «Сводная
// таблица» тут не пустые вкладки, а неверный вопрос: переписка и котировки
// живут у запросов, заведённых по выбранным аналогам.
//
// Отсюда три вкладки: подобрать и выбрать, посмотреть заведённое, свериться
// с условиями подбора.

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { RFQRead, RfqAnalogCandidate, RfqAnalogs } from "../api/types";

import IncotermPicker from "./IncotermPicker";
import { Field, HelpTip, Icon, Input, Term } from "./ui";

type TabKey = "picker" | "created" | "overview";

const TABS: { key: TabKey; label: string }[] = [
  { key: "picker", label: "Подбор аналогов" },
  { key: "created", label: "Заведённые запросы" },
  { key: "overview", label: "Обзор" },
];

const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

interface Props {
  rfq: RFQRead;
  onBack: () => void;
}

export default function AnalogRfqDetail({ rfq, onBack }: Props) {
  const navigate = useNavigate();
  // Вкладка живёт в адресе — так карточка открывается по ссылке и переживает
  // перезагрузку. Незнакомое имя откатывается к подбору: ради него пришли.
  const { tab: tabParam } = useParams();
  const tab: TabKey = TABS.some((item) => item.key === tabParam)
    ? (tabParam as TabKey)
    : "picker";
  const setTab = (key: TabKey) => navigate(`/requests/${rfq.id}/${key}`);

  const [analogs, setAnalogs] = useState<RfqAnalogs | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setAnalogs(await api.rfqAnalogs(rfq.id));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [rfq.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const candidates = analogs?.candidates ?? [];
  const created = candidates.filter((item) => item.created_rfq_id !== null);

  return (
    <div className="requests-page analog-page">
      <div className="detail-header">
        <button className="secondary back-btn" onClick={onBack}>
          ← К запросам
        </button>
        <h1>
          Запрос #{rfq.id} · {rfq.name}
        </h1>
        <Term
          label="подбор замены"
          help="Этот запрос поставщиков не ищет: он задаёт, чему подбирают замену. Поиск компаний идёт по запросам, заведённым из выбранных аналогов."
        />
      </div>

      <div className="tabs">
        {TABS.map((item) => (
          <button
            key={item.key}
            className={`tab${tab === item.key ? " active" : ""}`}
            onClick={() => setTab(item.key)}
          >
            {item.label}
            {item.key === "created" && created.length > 0 && (
              <span className="tab-count">{created.length}</span>
            )}
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p className="note">Загружаю подбор…</p>}

      {!loading && tab === "picker" && (
        <AnalogPicker
          rfq={rfq}
          analogs={analogs}
          onChanged={setAnalogs}
          onBatchCreated={(id) => navigate(`/requests/batch/${id}`)}
        />
      )}

      {!loading && tab === "created" && (
        <CreatedRequests
          created={created}
          onOpen={(id) => navigate(`/requests/${id}`)}
        />
      )}

      {!loading && tab === "overview" && <AnalogOverview rfq={rfq} />}
    </div>
  );
}

// --- вкладка «Подбор аналогов» ---

function AnalogPicker({
  rfq,
  analogs,
  onChanged,
  onBatchCreated,
}: {
  rfq: RFQRead;
  analogs: RfqAnalogs | null;
  onChanged: (value: RfqAnalogs) => void;
  onBatchCreated: (batchId: number) => void;
}) {
  const [chosen, setChosen] = useState<number[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdCount, setCreatedCount] = useState<number | null>(null);
  // Условия закупки спрашиваются здесь, а не в форме: пока неизвестно, какое
  // вещество закупают, объём и базис поставки называть не по чему.
  const [incoterms, setIncoterms] = useState<string[]>(["CIP", "FCA", "EXW"]);
  const [countries, setCountries] = useState<string[]>(["Китай"]);
  const [volume, setVolume] = useState("");

  const candidates = analogs?.candidates ?? [];
  const pending = candidates.filter((item) => item.created_rfq_id === null);

  const suggest = async () => {
    setSuggesting(true);
    setError(null);
    setCreatedCount(null);
    try {
      onChanged(await api.suggestRfqAnalogs(rfq.id));
      setChosen([]);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setSuggesting(false);
    }
  };

  const confirm = async () => {
    if (chosen.length === 0 || incoterms.length === 0 || countries.length === 0) {
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const result = await api.confirmRfqAnalogs(rfq.id, chosen, {
        incoterms,
        search_countries: countries,
        volume: volume.trim() || null,
      });
      onChanged(result.analogs);
      setChosen([]);
      setCreatedCount(result.batch?.created_count ?? 0);
      if (result.batch) onBatchCreated(result.batch.batch_id);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setCreating(false);
    }
  };

  const toggle = (id: number) =>
    setChosen((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );

  return (
    <div className="panel analog-picker">
      <p className="note">
        Ищем, чем заменить <strong>{rfq.name}</strong>
        {rfq.application ? ` — ${rfq.application}` : ""}. Отмеченные вещества
        станут отдельными запросами: у каждого свой поиск поставщиков и своя
        сводка.
      </p>

      <div className="analog-picker-actions">
        <button type="button" disabled={suggesting} onClick={() => void suggest()}>
          <Icon name="search" size={15} />
          {suggesting
            ? "Подбираю…"
            : analogs?.suggested_at
              ? "Подобрать заново"
              : "Подобрать аналоги"}
        </button>
        {suggesting && (
          <span className="note">
            Идёт поиск по перечням замен и разбор выдачи — это занимает
            полминуты.
          </span>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {analogs?.warnings.map((warning, index) => (
        <p className="analog-picker-warning" key={index}>
          {warning}
        </p>
      ))}

      {createdCount !== null && (
        <p className="analog-picker-created">
          {createdCount > 0
            ? `Заведено запросов: ${createdCount}. Поиск поставщиков по ним уже идёт.`
            : "По выбранным аналогам запросы уже были заведены — повтор ничего не задвоил."}
        </p>
      )}

      {/* Пустой список после подбора — допустимый ответ, и он честнее
          выдуманного названия. Но выглядеть как несработавшая кнопка он не
          должен: отметка о времени подбора отличает одно от другого. */}
      {analogs?.suggested_at && candidates.length === 0 && (
        <p className="note">
          Подходящих замен не нашлось. Так бывает у продуктов, которые
          выпускают по одной рецептуре: тогда искать нужно сам продукт —
          заведите обычный запрос без отметки «искать аналог».
        </p>
      )}

      {!analogs?.suggested_at && candidates.length === 0 && !suggesting && (
        <p className="note">
          Подбор ещё не запускался. Он ходит в поиск и в модель, поэтому
          начинается только по кнопке.
        </p>
      )}

      {pending.length > 0 && (
        <>
          <ul className="analog-list">
            {pending.map((candidate) => (
              <AnalogRow
                key={candidate.id}
                candidate={candidate}
                checked={chosen.includes(candidate.id)}
                onToggle={() => toggle(candidate.id)}
              />
            ))}
          </ul>

          {/* Условия закупки — здесь, а не в форме подбора: закупщик,
              который ещё не выбрал вещество, назвать объём и базис не
              может. Спрашиваются один раз на весь выбор. */}
          <div className="analog-terms">
            <div className="heading-with-help">
              <h4>Условия для заводимых запросов</h4>
              <HelpTip text="Применяются ко всем запросам, которые будут заведены по отмеченным аналогам. У каждого потом своя карточка — условия можно поправить в ней." />
            </div>
            <div className="analog-terms-row">
              <Field className="field-volume" label="Требуемый объём">
                <Input
                  placeholder="например, 20 т"
                  value={volume}
                  onChange={(event) => setVolume(event.target.value)}
                />
              </Field>
              <div className="field">
                <label>Страны поиска</label>
                <div className="checks">
                  {COUNTRY_OPTIONS.map((country) => (
                    <label key={country}>
                      <input
                        type="checkbox"
                        checked={countries.includes(country)}
                        onChange={() =>
                          setCountries((current) =>
                            current.includes(country)
                              ? current.filter((item) => item !== country)
                              : [...current, country],
                          )
                        }
                      />
                      {country}
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <div className="field">
              <label>Условия поставки</label>
              <IncotermPicker values={incoterms} onChange={setIncoterms} />
            </div>
          </div>

          <div className="analog-picker-actions">
            <button
              type="button"
              disabled={
                creating ||
                chosen.length === 0 ||
                incoterms.length === 0 ||
                countries.length === 0
              }
              title={
                chosen.length === 0
                  ? "Отметьте хотя бы один аналог"
                  : incoterms.length === 0
                    ? "Отметьте хотя бы одно условие поставки"
                    : countries.length === 0
                      ? "Выберите хотя бы одну страну поиска"
                      : undefined
              }
              onClick={() => void confirm()}
            >
              {creating
                ? "Завожу запросы…"
                : `Искать поставщиков по выбранным (${chosen.length})`}
            </button>
            <span className="note">
              По каждому отмеченному веществу создаётся отдельный запрос со
              своим поиском.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

// Один кандидат. Цитата и ссылка показаны рядом с названием, а не спрятаны
// под раскрытие: «аналог» ошибкой обходится дороже прочего, и проверять его
// закупщик должен до выбора, а не после.
function AnalogRow({
  candidate,
  checked,
  onToggle,
}: {
  candidate: RfqAnalogCandidate;
  checked: boolean;
  onToggle: () => void;
}) {
  let host: string | null = null;
  if (candidate.source_url) {
    try {
      host = new URL(candidate.source_url).hostname.replace(/^www\./, "");
    } catch {
      host = candidate.source_url;
    }
  }

  return (
    <li className={`analog-item${checked ? " is-chosen" : ""}`}>
      <label className="analog-item-head">
        <input type="checkbox" checked={checked} onChange={onToggle} />
        <span className="analog-item-name">{candidate.name}</span>
        {candidate.cas ? (
          <span className="badge ok" title="Номер подтверждён источником">
            CAS {candidate.cas}
          </span>
        ) : (
          <span className="badge muted" title="Номера в источнике не нашлось">
            без CAS
          </span>
        )}
      </label>
      {candidate.reason && <p className="analog-item-reason">{candidate.reason}</p>}
      {candidate.quote && (
        <blockquote className="analog-item-quote">{candidate.quote}</blockquote>
      )}
      {candidate.source_url && (
        <a
          className="analog-item-source"
          href={candidate.source_url}
          target="_blank"
          rel="noreferrer"
        >
          {host}
        </a>
      )}
    </li>
  );
}

// --- вкладка «Заведённые запросы» ---

function CreatedRequests({
  created,
  onOpen,
}: {
  created: RfqAnalogCandidate[];
  onOpen: (rfqId: number) => void;
}) {
  if (created.length === 0) {
    return (
      <div className="panel">
        <p className="note">
          Пока ничего не заведено. Отметьте подходящие аналоги на вкладке
          «Подбор аналогов» — по каждому появится отдельный запрос.
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <p className="note">
        Каждый запрос независим: свой поиск, своя переписка, своя котировка.
        Здесь видно только, что из этого подбора выросло.
      </p>
      <table className="analog-created-table">
        <thead>
          <tr>
            <th>Вещество</th>
            <th>CAS</th>
            <th>Почему выбран</th>
          </tr>
        </thead>
        <tbody>
          {created.map((candidate) => (
            <tr key={candidate.id}>
              <td>
                <button
                  className="link-btn"
                  type="button"
                  onClick={() => onOpen(candidate.created_rfq_id as number)}
                >
                  {candidate.name}
                </button>
              </td>
              <td className="cas">{candidate.cas ?? "—"}</td>
              <td className="analog-created-reason">{candidate.reason || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- вкладка «Обзор» ---

function AnalogOverview({ rfq }: { rfq: RFQRead }) {
  return (
    <div className="panel">
      <h3>Условия подбора</h3>
      <dl className="params-list">
        <dt className="param-label">
          <Term
            label="Что заменяем"
            help="Позиция, для которой ищут замену. В запросы к поставщикам она не уходит: уходят выбранные аналоги."
          />
        </dt>
        <dd className="param-value">{rfq.name}</dd>

        <dt className="param-label">CAS</dt>
        <dd className="param-value">{rfq.cas || "не указан"}</dd>

        <dt className="param-label">
          <Term
            label="Для чего используется"
            help="Главный критерий подбора: у одного вещества в разных отраслях разные заменители."
          />
        </dt>
        <dd className="param-value">{rfq.application || "не указано"}</dd>

        <dt className="param-label">Показатели замены</dt>
        <dd className="param-value">{rfq.specification || "не указаны"}</dd>

        <dt className="param-label">
          <Term
            label="Чем заменять нельзя"
            help="Запрет закупщика. Кандидат, который ему противоречит, в подборе не показывается."
          />
        </dt>
        <dd className="param-value">{rfq.analog_constraints || "ограничений нет"}</dd>

        {rfq.specialist_comment && (
          <>
            <dt className="param-label">Комментарий</dt>
            <dd className="param-value">{rfq.specialist_comment}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
