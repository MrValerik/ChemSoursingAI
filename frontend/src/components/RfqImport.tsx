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
import type { RfqImportPreview, RfqImportRow } from "../api/types";

import { HelpTip, Icon } from "./ui";
// Строка предпросмотра плюс признак «правится прямо сейчас». Признак живёт
// только на экране: сервер о черновике правки ничего не знает, он получает
// строку целиком, когда правка завершена.
type EditableRow = RfqImportRow & { dirty?: boolean };

// Колонки предпросмотра. Правятся те поля, в которых закупщик чаще всего
// ошибается при выгрузке: название, номер, объём с единицей.
const EDITABLE: { key: string; label: string; width?: string }[] = [
  { key: "name", label: "Название" },
  { key: "cas", label: "CAS", width: "130px" },
  { key: "volume", label: "Объём", width: "110px" },
  { key: "unit", label: "Ед.", width: "80px" },
];

interface Props {
  /** Разобранные и не исключённые строки — их заберёт пакетное создание. */
  onReady?: (rows: RfqImportRow[]) => void;
}

export default function RfqImport({ onReady }: Props) {
  const [preview, setPreview] = useState<RfqImportPreview | null>(null);
  const [rows, setRows] = useState<EditableRow[]>([]);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isExcluded = (row: number) => excluded.includes(row);

  const publish = (next: EditableRow[], skipped: number[]) => {
    onReady?.(next.filter((row) => row.importable && !skipped.includes(row.row)));
  };

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.previewRfqImport(file);
      setPreview(result);
      setRows(result.rows);
      setExcluded([]);
      setFileName(file.name);
      publish(result.rows, []);
    } catch (caught) {
      setPreview(null);
      setRows([]);
      setError(caught instanceof ApiError ? caught.message : String(caught));
      onReady?.([]);
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
      setRows((list) => {
        const next = list.map((item) =>
          item.row === row.row ? { ...checked, dirty: false } : item,
        );
        publish(next, excluded);
        return next;
      });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setRechecking(null);
    }
  };

  const toggleExcluded = (row: number) => {
    setExcluded((current) => {
      const next = current.includes(row)
        ? current.filter((item) => item !== row)
        : [...current, row];
      publish(rows, next);
      return next;
    });
  };

  const ready = rows.filter((row) => row.importable && !isExcluded(row.row));
  const broken = rows.filter((row) => !row.importable);

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
        {fileName && !busy && <span className="rfq-import-file">{fileName}</span>}
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
            {broken.length > 0 && <> · с ошибками: <strong>{broken.length}</strong></>}
            {excluded.length > 0 && <> · исключено: <strong>{excluded.length}</strong></>}
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
                          <p className="rfq-import-row-warning" key={`w${index}`}>
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
              Строки с ошибками в запросы не попадут. Исправьте значение прямо
              в таблице — проверка повторится — или оставьте как есть и
              создайте остальные.
            </p>
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
  if (values.search_countries?.length) parts.push(values.search_countries.join(", "));
  if (values.confirmed_synonyms?.length) {
    parts.push(`синонимы: ${values.confirmed_synonyms.join(", ")}`);
  }
  return <span className="rfq-import-values">{parts.join(" · ") || "—"}</span>;
}
