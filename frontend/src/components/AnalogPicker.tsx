// Подбор аналогов: первая ступень запроса «нужна замена».
//
// Между «нужна замена» и «ищем поставщиков» не хватало шага, на котором
// замену называют по имени. Без него решение принимал поставщик: присылал
// то, что считал заменой сам, и закупщик узнавал об этом из ответа. Здесь
// система называет вещества-кандидаты, показывает доказательство по
// каждому, а выбирает человек.
//
// Ничего не выбирается само и ничего не запускается молча. Подбор ходит в
// сеть и в модель, поэтому идёт по кнопке, а не при открытии вкладки:
// открытая карточка не должна тратить поисковую квоту.

import { useEffect, useState } from "react";

import { api, ApiError } from "../api/client";
import type { RfqAnalogCandidate, RfqAnalogs } from "../api/types";

import { HelpTip, Icon } from "./ui";

interface Props {
  rfqId: number;
  /** Название позиции, замену которой ищем. */
  name: string;
  /** Заведены запросы по выбранным аналогам — можно открыть их сводку. */
  onBatchCreated?: (batchId: number) => void;
  /** Открыть запрос, заведённый по аналогу. */
  onOpenRfq?: (rfqId: number) => void;
}

export default function AnalogPicker({
  rfqId,
  name,
  onBatchCreated,
  onOpenRfq,
}: Props) {
  const [analogs, setAnalogs] = useState<RfqAnalogs | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [suggesting, setSuggesting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdCount, setCreatedCount] = useState<number | null>(null);

  // Уже подобранное показывается сразу: подбор стоит поискового запроса, и
  // повторять его при каждом открытии вкладки нельзя.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .rfqAnalogs(rfqId)
      .then((loaded) => {
        if (cancelled) return;
        setAnalogs(loaded);
        setChosen(
          loaded.candidates
            .filter((item) => item.selected && item.created_rfq_id === null)
            .map((item) => item.id),
        );
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : String(caught));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rfqId]);

  const suggest = async () => {
    setSuggesting(true);
    setError(null);
    setCreatedCount(null);
    try {
      const loaded = await api.suggestRfqAnalogs(rfqId);
      setAnalogs(loaded);
      setChosen([]);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setSuggesting(false);
    }
  };

  const toggle = (id: number) => {
    setChosen((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  };

  const confirm = async () => {
    if (chosen.length === 0) return;
    setCreating(true);
    setError(null);
    try {
      const result = await api.confirmRfqAnalogs(rfqId, chosen);
      setAnalogs(result.analogs);
      setChosen([]);
      setCreatedCount(result.batch?.created_count ?? 0);
      if (result.batch && onBatchCreated) onBatchCreated(result.batch.batch_id);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return <p className="note">Загружаю подбор аналогов…</p>;
  }

  const candidates = analogs?.candidates ?? [];
  const pending = candidates.filter((item) => item.created_rfq_id === null);
  const started = candidates.filter((item) => item.created_rfq_id !== null);

  return (
    <div className="analog-picker panel">
      <div className="heading-with-help">
        <h3>Подбор аналогов</h3>
        <HelpTip text="Поиск компаний по этому запросу не идёт: сначала нужно решить, чем заменять. Система предлагает вещества с цитатой из источника, вы отмечаете подходящие — и на каждое отмеченное заводится отдельный запрос со своим поиском." />
      </div>

      <p className="note">
        Ищем, чем заменить <strong>{name}</strong>. Отмеченные вещества
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
          выдуманного названия. Но выглядеть как несработавшая кнопка он
          не должен: отметка о времени подбора отличает одно от другого. */}
      {analogs?.suggested_at && candidates.length === 0 && (
        <p className="note">
          Подходящих замен не нашлось. Так бывает у продуктов, которые
          выпускают по одной рецептуре: тогда искать нужно сам продукт —
          снимите в карточке отметку «искать аналог».
        </p>
      )}

      {!analogs?.suggested_at && candidates.length === 0 && !suggesting && (
        <p className="note">
          Подбор ещё не запускался. Он ходит в поиск и в модель, поэтому
          начинается только по кнопке.
        </p>
      )}

      {pending.length > 0 && (
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
      )}

      {pending.length > 0 && (
        <div className="analog-picker-actions">
          <button
            type="button"
            disabled={creating || chosen.length === 0}
            title={
              chosen.length === 0 ? "Отметьте хотя бы один аналог" : undefined
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
      )}

      {started.length > 0 && (
        <div className="analog-started">
          <h4>Запросы по выбранным аналогам</h4>
          <ul>
            {started.map((candidate) => (
              <li key={candidate.id}>
                <button
                  className="link-btn"
                  type="button"
                  onClick={() => onOpenRfq?.(candidate.created_rfq_id as number)}
                >
                  {candidate.name}
                </button>
                {candidate.cas && <span className="cas"> · CAS {candidate.cas}</span>}
              </li>
            ))}
          </ul>
        </div>
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
