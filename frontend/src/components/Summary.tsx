import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  PurchaseDecisionRead,
  RFQRead,
  SummaryRow,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import DispatchTab from "./DispatchTab";
import { Textarea } from "./ui";

interface Props {
  rfq: RFQRead;
  refreshKey?: number;
}

const formatPrice = (value: number | null, currency: string | null) => {
  if (value === null) return "—";
  const amount = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 4,
  }).format(value);
  return `${amount}${currency ? ` ${currency}` : ""}`;
};

const formatMoment = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

const supplierName = (row: SummaryRow) => row.supplier ?? row.manager ?? "—";

const costCurrency = (row: SummaryRow) => row.cost_currency ?? row.currency;

const pricePerUnit = (row: SummaryRow) => formatPrice(row.price, row.currency);

const documentsLabel = (row: SummaryRow) => {
  const documents = [row.has_coa ? "CoA" : null, row.has_tds ? "TDS" : null].filter(
    Boolean,
  );
  return documents.length > 0 ? documents.join(" · ") : "нет";
};

const confidenceLabel = (row: SummaryRow) => {
  const values = Object.values(row.field_confidence ?? {}).filter((value) =>
    Number.isFinite(value),
  );
  if (values.length === 0) return "не указана";
  return `${Math.round(Math.min(...values) * 100)}% минимум`;
};

export default function Summary({ rfq, refreshKey = 0 }: Props) {
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";
  const [rows, setRows] = useState<SummaryRow[]>([]);
  const [decision, setDecision] = useState<PurchaseDecisionRead | null>(null);
  const [selectedQuotationId, setSelectedQuotationId] = useState<number | null>(
    null,
  );
  const [decisionNote, setDecisionNote] = useState("");
  const [onlyComplete, setOnlyComplete] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([api.getSummary(rfq.id), api.getPurchaseDecision(rfq.id)])
      .then(([summaryRows, savedDecision]) => {
        if (cancelled) return;
        setRows(summaryRows);
        setDecision(savedDecision);
        setSelectedQuotationId(
          savedDecision?.quotation_id ?? summaryRows[0]?.quotation_id ?? null,
        );
        setDecisionNote(savedDecision?.note ?? "");
        setError(null);
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rfq.id, refreshKey]);

  const shown = onlyComplete ? rows.filter((row) => row.is_complete) : rows;
  const selectedRow =
    rows.find((row) => row.quotation_id === selectedQuotationId) ?? null;
  const dialogueRow = selectedRow;
  const currencies = useMemo(
    () => Array.from(new Set(rows.map((row) => row.currency).filter(Boolean))),
    [rows],
  );
  const decisionChanged =
    selectedQuotationId !== (decision?.quotation_id ?? null) ||
    decisionNote.trim() !== (decision?.note ?? "");

  const saveDecision = async () => {
    if (!selectedRow || readOnly) return;
    if (
      !selectedRow.is_complete &&
      !window.confirm(
        "В выбранном предложении не хватает обязательных условий. Всё равно сохранить его как итог закупки?",
      )
    ) {
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await api.savePurchaseDecision(rfq.id, {
        quotation_id: selectedRow.quotation_id,
        note: decisionNote.trim() || null,
      });
      setDecision(saved);
      setDecisionNote(saved.note ?? "");
      setNotice("Итог закупки сохранён.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  };

  const selectQuotation = (row: SummaryRow) => {
    setSelectedQuotationId(row.quotation_id);
    setDecisionNote(
      row.quotation_id === decision?.quotation_id ? (decision.note ?? "") : "",
    );
    setNotice(null);
  };

  return (
    <div className="summary-workspace">
      <section className="panel summary-comparison-panel">
        <div className="summary-heading">
          <div>
            <h2>Сводная сравнительная таблица</h2>
            <p className="note">
              Сравните условия и вручную отметьте предложение для итоговой закупки.
            </p>
          </div>
          <label className="summary-complete-filter">
            <input
              checked={onlyComplete}
              onChange={(event) => setOnlyComplete(event.target.checked)}
              type="checkbox"
            />
            Только полные
          </label>
        </div>

        {error && <p className="error">{error}</p>}
        {notice && <p className="success-note">{notice}</p>}
        {loading ? (
          <p className="note">Загружаем предложения…</p>
        ) : (
          <>
            {currencies.length > 1 && (
              <p className="summary-warning">
                Цены указаны в разных валютах и пока не нормализованы — сравнивать
                их напрямую нельзя.
              </p>
            )}

            {shown.length === 0 ? (
              <p className="note">
                {rows.length === 0
                  ? "Котировок пока нет — получите ответы поставщиков в разделе «Общение»."
                  : "Полных котировок пока нет. Отключите фильтр, чтобы увидеть неполные ответы."}
              </p>
            ) : (
              <div
                aria-label="Сравнительная таблица предложений поставщиков"
                className="summary-table-frame"
                role="region"
                tabIndex={0}
              >
                <table className="summary summary-detailed-table">
                  <thead>
                    <tr>
                      <th>Итог</th>
                      <th>Поставщик</th>
                      <th>Производитель</th>
                      <th>Страна</th>
                      <th>Фасовка</th>
                      <th>Грейд</th>
                      <th>HAZMAT</th>
                      <th>Цена</th>
                      <th>Единица</th>
                      <th>Объём</th>
                      <th>MOQ</th>
                      <th>Закупка</th>
                      <th>Доставка</th>
                      <th>Пошлина</th>
                      <th>НДС</th>
                      <th>Итого до склада</th>
                      <th>Incoterm</th>
                      <th>Оплата</th>
                      <th>Срок</th>
                      <th>Документы</th>
                      <th>Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((row) => (
                      <tr
                        className={`${row.is_complete ? "" : "incomplete"} ${
                          row.quotation_id === selectedQuotationId
                            ? "summary-selected-row"
                            : ""
                        }`}
                        key={row.quotation_id}
                      >
                        <td>
                          <input
                            aria-label={`Выбрать предложение ${supplierName(row)}`}
                            checked={row.quotation_id === selectedQuotationId}
                            disabled={readOnly}
                            name="purchase-decision"
                            onChange={() => selectQuotation(row)}
                            type="radio"
                          />
                        </td>
                        <td>
                          <button
                            aria-pressed={row.quotation_id === selectedQuotationId}
                            className="summary-supplier-button"
                            onClick={() => selectQuotation(row)}
                            type="button"
                          >
                            <strong>{supplierName(row)}</strong>
                          </button>
                        </td>
                        <td>{row.manufacturer ?? "—"}</td>
                        <td>{row.origin_country ?? "—"}</td>
                        <td>{row.packaging ?? "—"}</td>
                        <td>{row.grade ?? "—"}</td>
                        <td>
                          {row.is_hazmat === null
                            ? "—"
                            : row.is_hazmat
                              ? "да"
                              : "нет"}
                        </td>
                        <td>{pricePerUnit(row)}</td>
                        <td>{row.price_unit ?? "—"}</td>
                        <td>{row.quoted_quantity ?? "—"}</td>
                        <td>{row.moq ?? "—"}</td>
                        <td>{formatPrice(row.total_price, costCurrency(row))}</td>
                        <td>{formatPrice(row.delivery_cost, costCurrency(row))}</td>
                        <td>{formatPrice(row.duty_cost, costCurrency(row))}</td>
                        <td>{formatPrice(row.vat_cost, costCurrency(row))}</td>
                        <td>{formatPrice(row.landed_cost, costCurrency(row))}</td>
                        <td>{row.incoterm ?? "—"}</td>
                        <td>{row.payment_terms ?? "—"}</td>
                        <td>{row.lead_time ?? "—"}</td>
                        <td>{documentsLabel(row)}</td>
                        <td>
                          <span className={`badge ${row.is_complete ? "tone-ok" : "tone-warn"}`}>
                            {row.is_complete ? "полная" : "неполная"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      <section className="panel purchase-result">
        <div className="purchase-result-heading">
          <div>
            <h2>Итог закупки</h2>
            <p className="note">
              Финальное предложение выбирает сотрудник. Это решение не отправляет
              заказ, не заключает договор и не проводит оплату.
            </p>
          </div>
          {decision && <span className="badge tone-ok">выбор сохранён</span>}
        </div>

        {!selectedRow ? (
          <p className="note">
            Выберите предложение в таблице выше, чтобы сформировать итог закупки.
          </p>
        ) : (
          <>
            <div className="purchase-result-grid">
              <div><span>Товар</span><strong>{rfq.name}</strong></div>
              <div><span>Планируемый объём</span><strong>{rfq.volume ?? "не указан"}</strong></div>
              <div><span>Поставщик</span><strong>{supplierName(selectedRow)}</strong></div>
              <div><span>Производитель</span><strong>{selectedRow.manufacturer ?? "не указан"}</strong></div>
              <div><span>Страна закупки</span><strong>{selectedRow.origin_country ?? "не указана"}</strong></div>
              <div><span>Фасовка</span><strong>{selectedRow.packaging ?? "не указана"}</strong></div>
              <div><span>Предложенная цена</span><strong>{pricePerUnit(selectedRow)}</strong></div>
              <div><span>Предложенный объём</span><strong>{selectedRow.quoted_quantity ?? "не указан"}</strong></div>
              <div><span>Стоимость закупки</span><strong>{formatPrice(selectedRow.total_price, costCurrency(selectedRow))}</strong></div>
              <div><span>Доставка</span><strong>{formatPrice(selectedRow.delivery_cost, costCurrency(selectedRow))}</strong></div>
              <div><span>Пошлина</span><strong>{formatPrice(selectedRow.duty_cost, costCurrency(selectedRow))}</strong></div>
              <div><span>НДС</span><strong>{formatPrice(selectedRow.vat_cost, costCurrency(selectedRow))}</strong></div>
              <div><span>Итого до склада</span><strong>{formatPrice(selectedRow.landed_cost, costCurrency(selectedRow))}</strong></div>
              <div><span>Базис поставки</span><strong>{selectedRow.incoterm ?? "не указан"}</strong></div>
              <div><span>MOQ</span><strong>{selectedRow.moq ?? "не указан"}</strong></div>
              <div><span>Условия оплаты</span><strong>{selectedRow.payment_terms ?? "не указаны"}</strong></div>
              <div><span>Срок</span><strong>{selectedRow.lead_time ?? "не указан"}</strong></div>
              <div><span>Документы</span><strong>{documentsLabel(selectedRow)}</strong></div>
              <div><span>HAZMAT</span><strong>{selectedRow.is_hazmat === null ? "не указано" : selectedRow.is_hazmat ? "да" : "нет"}</strong></div>
              <div><span>Уверенность извлечения</span><strong>{confidenceLabel(selectedRow)}</strong></div>
              <div><span>Получено</span><strong>{formatMoment(selectedRow.created_at)}</strong></div>
              <div><span>Полнота</span><strong>{selectedRow.is_complete ? "полная котировка" : "есть недостающие условия"}</strong></div>
            </div>

            {selectedRow.landed_cost === null && (
              <p className="summary-warning">
                Итоговая себестоимость пока не подтверждена. Система не перемножает
                цену и объём без явной единицы и не придумывает доставку, пошлину или НДС.
              </p>
            )}

            {readOnly ? (
              <div className="purchase-decision-note">
                <span>Комментарий к выбору</span>
                <p>{decision?.note ?? "Комментарий не указан."}</p>
              </div>
            ) : (
              <label className="purchase-decision-note">
                <span>Комментарий к выбору</span>
                <Textarea
                  maxLength={2000}
                  placeholder="Например: согласовано после проверки CoA и условий оплаты"
                  rows={3}
                  value={decisionNote}
                  onChange={(event) => {
                    setDecisionNote(event.target.value);
                    setNotice(null);
                  }}
                />
              </label>
            )}

            <div className="purchase-result-footer">
              <span className="note">
                {decision
                  ? `Последнее решение: ${decision.selected_by_name ?? "сотрудник"}, ${formatMoment(decision.updated_at)}`
                  : "Итог ещё не сохранён"}
              </span>
              {!readOnly && (
                <button
                  disabled={saving || !decisionChanged}
                  onClick={() => void saveDecision()}
                  type="button"
                >
                  {saving ? "Сохраняем…" : "Сохранить итог закупки"}
                </button>
              )}
            </div>
          </>
        )}
      </section>

      {dialogueRow && (
        <DispatchTab
          compact
          focusedChannel={dialogueRow.conversation_channel}
          focusedManagerId={dialogueRow.manager_id}
          focusedSupplierId={dialogueRow.supplier_id}
          focusedTestRunId={dialogueRow.test_run_id}
          key={`summary-dialogue-${dialogueRow.quotation_id}`}
          rfq={rfq}
          onStatusChanged={() => {
            void api
              .getSummary(rfq.id)
              .then(setRows)
              .catch((caught) => {
                setError(
                  caught instanceof Error ? caught.message : String(caught),
                );
              });
          }}
        />
      )}
    </div>
  );
}
