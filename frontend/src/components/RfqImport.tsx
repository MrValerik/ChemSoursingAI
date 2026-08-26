// Импорт списка позиций из XLSX/CSV.
//
// Закупщик работает списком на 5–50 веществ, и создавать запрос на каждую
// позицию руками — это ровно та работа, ради избавления от которой он
// пришёл. Экран отвечает на один вопрос: что именно система прочитала в
// файле и с какими оговорками.
//
// Ничего не создаётся и не сохраняется: ни запросов, ни самого файла.
// Список сырья — коммерческая тайна закупщика, и пока нет решения о сроке
// хранения, самый безопасный файл — тот, которого нет на диске.
//
// Ошибка одной строки не прячет остальные. Файл на 50 позиций с одним
// неверным номером обязан показать 49 готовых строк и одну проблемную с
// номером строки, полем и причиной.

import { useRef, useState } from "react";

import { api, ApiError } from "../api/client";
import type {
  RfqBatchCreateResult,
  RfqImportPreview,
  RfqImportRow,
} from "../api/types";

import { HelpTip, Icon } from "./ui";
// Строка предпросмотра плюс признак «правится прямо сейчас». Признак живёт
// только на экране: сервер о черновике правки ничего не знает, он получает
// строку целиком, когда правка завершена.
type EditableRow = RfqImportRow & { dirty?: boolean };

// Ключ идемпотентности. crypto.randomUUID есть не во всех контекстах
// (например, по http на не-localhost), поэтому запасной вариант обязателен:
// без ключа повторное нажатие завело бы второй набор запросов.
const newKey = (): string => {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid;
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
};

// Колонки предпросмотра. Правятся те поля, в которых закупщик чаще всего
// ошибается при выгрузке: название, номер, объём с единицей.
// Условия закупки, общие для всего списка. В файле закупщика колонок
// «Incoterms» и «Страны» обычно нет, а без них запрос не создаётся. Показаны
// явно: молча подставить базис поставки на весь список нельзя — от него
// зависит, кто платит за перевозку.
const BATCH_INCOTERMS = ["EXW", "FCA", "FOB", "CIP", "DAP"];
const BATCH_COUNTRIES = ["Россия", "Китай", "Индия"];

const EDITABLE: { key: string; label: string; width?: string }[] = [
  { key: "name", label: "Название" },
  { key: "cas", label: "CAS", width: "130px" },
  { key: "volume", label: "Объём", width: "110px" },
  { key: "unit", label: "Ед.", width: "80px" },
];

interface Props {
  /** Пакет создан — можно открыть его сводку. */
  onCreated?: (batchId: number) => void;
}

export default function RfqImport({ onCreated }: Props) {
  const [preview, setPreview] = useState<RfqImportPreview | null>(null);
  const [rows, setRows] = useState<EditableRow[]>([]);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [incoterms, setIncoterms] = useState<string[]>(["CIP", "FCA", "EXW"]);
  const [countries, setCountries] = useState<string[]>(["Китай"]);
  const [result, setResult] = useState<RfqBatchCreateResult | null>(null);
  // Ключ идемпотентности живёт вместе с разобранным файлом: повторное
  // нажатие и повтор после обрыва ответа приходят с тем же ключом и не
  // создают второй набор запросов. Новый файл — новый ключ.
  const idempotencyKey = useRef<string>("");
  const inputRef = useRef<HTMLInputElement>(null);

  const isExcluded = (row: number) => excluded.includes(row);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.previewRfqImport(file);
      setPreview(result);
      setRows(result.rows);
      setResult(null);
      idempotencyKey.current = newKey();
      setExcluded([]);
      setFileName(file.name);
    } catch (caught) {
      setPreview(null);
      setRows([]);
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
      // Иначе повторный выбор того же файла не вызовет onChange.
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  // Набранное видно сразу, но на сервер не уходит: перепроверять каждую
  // букву — это сетевой запрос на нажатие клавиши.
  const editDraft = (row: EditableRow, key: string, value: string) => {
    setRows((current) =>
      current.map((item) =>
        item.row === row.row
          ? { ...item, raw: { ...item.raw, [key]: value }, dirty: true }
          : item,
      ),
    );
  };

  // Исправленное значение проходит ту же проверку, что и прочитанное из
  // файла: правила разбора живут на сервере в одном месте, и повторять их
  // здесь значит гарантировать расхождение.
  const commitEdit = async (row: EditableRow) => {
    const current = rows.find((item) => item.row === row.row);
    if (!current?.dirty) return;
    setRechecking(row.row);
    try {
      const checked = await api.recheckRfqImportRow(row.row, current.raw);
      setRows((list) =>
        list.map((item) =>
          item.row === row.row ? { ...checked, dirty: false } : item,
        ),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setRechecking(null);
    }
  };

  const toggleExcluded = (row: number) => {
    setExcluded((current) =>
      current.includes(row)
        ? current.filter((item) => item !== row)
        : [...current, row],
    );
  };

  const ready = rows.filter((row) => row.importable && !isExcluded(row.row));
  const broken = rows.filter((row) => !row.importable);

  const createBatch = async () => {
    if (!ready.length) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api.createRfqBatch({
        idempotency_key: idempotencyKey.current,
        source_name: fileName || null,
        defaults: { incoterms, search_countries: countries },
        items: ready.map((row) => ({
          row: row.row,
          values: row.values as Record<string, unknown>,
        })),
      });
      setResult(created);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="rfq-import">
      <div className="rfq-import-head">
        <label className="rfq-import-pick">
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xlsm,.csv,.txt"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
            }}
          />
          <span className="rfq-import-pick-label">
            <Icon name="flask" size={15} />
            {busy ? "Читаю файл…" : "Загрузить список из XLSX или CSV"}
          </span>
        </label>
        <HelpTip text="Файл разбирается на сервере детерминированно и нигде не сохраняется: ни как документ, ни в журнале. В нейросеть он не отправляется. Ожидаются колонки «Название» (обязательна), CAS, объём, единица, чистота, грейд, синонимы, спецификация, цена, валюта, Incoterms, страны, комментарий — на русском или английском." />
        {fileName && !busy && (
          <span className="rfq-import-file">{fileName}</span>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {preview && (
        <>
          {preview.file_warnings.map((item, index) => (
            <p className="rfq-import-warning" key={index}>
              {item.message}
            </p>
          ))}

          <p className="rfq-import-summary">
            Прочитано строк: <strong>{preview.total_rows}</strong>. Готовы к
            созданию: <strong>{ready.length}</strong>
            {broken.length > 0 && (
              <>
                {" "}
                · с ошибками: <strong>{broken.length}</strong>
              </>
            )}
            {excluded.length > 0 && (
              <>
                {" "}
                · исключено: <strong>{excluded.length}</strong>
              </>
            )}
          </p>

          <div className="rfq-import-table-wrap">
            <table className="rfq-import-table">
              <thead>
                <tr>
                  <th className="rfq-import-col-take">
                    <span title="Создавать запрос по этой строке">Брать</span>
                  </th>
                  <th className="rfq-import-col-row">Стр.</th>
                  {EDITABLE.map((column) => (
                    <th key={column.key} style={{ width: column.width }}>
                      {column.label}
                    </th>
                  ))}
                  <th>Что прочитано</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const skipped = isExcluded(row.row);
                  return (
                    <tr
                      key={row.row}
                      className={[
                        !row.importable ? "is-broken" : "",
                        skipped ? "is-skipped" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    >
                      <td className="rfq-import-col-take">
                        <input
                          type="checkbox"
                          aria-label={`Строка ${row.row}: создавать запрос`}
                          checked={row.importable && !skipped}
                          disabled={!row.importable}
                          onChange={() => toggleExcluded(row.row)}
                        />
                      </td>
                      <td className="rfq-import-col-row">{row.row}</td>
                      {EDITABLE.map((column) => (
                        <td key={column.key}>
                          {/* Поле управляемое: набранное сразу видно в
                              строке, а перепроверка на сервере идёт по
                              завершении правки — по Enter или уходу
                              фокуса, а не на каждую букву. */}
                          <input
                            className="rfq-import-cell"
                            aria-label={`Строка ${row.row}, ${column.label}`}
                            value={row.raw[column.key] ?? ""}
                            disabled={rechecking === row.row}
                            onChange={(event) =>
                              editDraft(row, column.key, event.target.value)
                            }
                            onBlur={() => void commitEdit(row)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                void commitEdit(row);
                              }
                            }}
                          />
                        </td>
                      ))}
                      <td className="rfq-import-read">
                        {row.importable ? (
                          <ReadValues row={row} />
                        ) : (
                          <span className="rfq-import-none">—</span>
                        )}
                        {row.errors.map((item, index) => (
                          <p className="rfq-import-row-error" key={`e${index}`}>
                            {item.message}
                          </p>
                        ))}
                        {row.warnings.map((item, index) => (
                          <p
                            className="rfq-import-row-warning"
                            key={`w${index}`}
                          >
                            {item.message}
                          </p>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {broken.length > 0 && (
            <p className="rfq-import-hint">
              Строки с ошибками в запросы не попадут. Исправьте значение прямо в
              таблице — проверка повторится — или оставьте как есть и создайте
              остальные.
            </p>
          )}

          {result ? (
            <div className="rfq-import-result">
              <p className="rfq-import-summary">
                {result.created
                  ? "Пакет создан."
                  : "Пакет уже был создан этим же действием — повтор ничего не задвоил."}{" "}
                Запросов: <strong>{result.created_count}</strong> · поисков в
                очереди: <strong>{result.search_runs}</strong>
                {result.failed_count > 0 && (
                  <>
                    {" "}
                    · не создано: <strong>{result.failed_count}</strong>
                  </>
                )}
              </p>
              {result.results
                .filter((item) => item.error)
                .map((item) => (
                  <p className="rfq-import-row-error" key={item.row}>
                    Строка {item.row} · {item.name}: {item.error}
                  </p>
                ))}
              <button onClick={() => onCreated?.(result.batch_id)}>
                Открыть сводку пакета
              </button>
            </div>
          ) : (
            <>
              <div className="rfq-import-defaults">
                <div className="field">
                  <div className="heading-with-help">
                    <label>Условия поставки для всего списка</label>
                    <HelpTip text="Применяются к позициям, у которых в файле нет колонки Incoterms. Если базис указан в самой строке, действует он." />
                  </div>
                  <div className="checks">
                    {BATCH_INCOTERMS.map((code) => (
                      <label key={code}>
                        <input
                          type="checkbox"
                          checked={incoterms.includes(code)}
                          onChange={() =>
                            setIncoterms((current) =>
                              current.includes(code)
                                ? current.filter((item) => item !== code)
                                : [...current, code],
                            )
                          }
                        />
                        {code}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="field">
                  <div className="heading-with-help">
                    <label>Страны поиска для всего списка</label>
                    <HelpTip text="Применяются к позициям, у которых в файле нет колонки со странами. Указанное в строке сильнее." />
                  </div>
                  <div className="checks">
                    {BATCH_COUNTRIES.map((country) => (
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

              <div className="rfq-import-actions">
                <button
                  disabled={
                    creating || ready.length === 0 || incoterms.length === 0
                  }
                  onClick={() => void createBatch()}
                  title={
                    ready.length === 0
                      ? "Нет ни одной строки, готовой к созданию"
                      : incoterms.length === 0
                        ? "Отметьте хотя бы одно условие поставки"
                        : undefined
                  }
                >
                  {creating
                    ? "Создаю запросы…"
                    : `Создать ${ready.length} запрос(ов) и начать поиск`}
                </button>
                <span className="rfq-import-hint">
                  По каждой позиции создаётся отдельный запрос со своим поиском.
                </span>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

// Что именно система поняла из строки. Показывается разобранное значение,
// а не исходная ячейка: «2 т» в файле и «2 t» в запросе — разные строки, и
// закупщик должен увидеть вторую до создания запроса.
function ReadValues({ row }: { row: RfqImportRow }) {
  const parts: string[] = [];
  const values = row.values;
  if (values.cas) parts.push(`CAS ${values.cas}`);
  else if (values.identification_method === "spec") parts.push("без CAS");
  if (values.volume) parts.push(values.volume);
  if (values.purity) parts.push(values.purity);
  if (values.target_price != null) {
    parts.push(`${values.target_price} ${values.currency ?? "USD"}`);
  }
  if (values.incoterms?.length) parts.push(values.incoterms.join(", "));
  if (values.search_countries?.length)
    parts.push(values.search_countries.join(", "));
  if (values.confirmed_synonyms?.length) {
    parts.push(`синонимы: ${values.confirmed_synonyms.join(", ")}`);
  }
  return <span className="rfq-import-values">{parts.join(" · ") || "—"}</span>;
}
