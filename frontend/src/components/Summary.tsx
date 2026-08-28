import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type {
  PurchaseDecisionRead,
  QuotationUpdate,
  RFQRead,
  SummaryRow,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import DispatchTab from "./DispatchTab";
import { IconButton, Input, Textarea } from "./ui";

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

const manufacturerRoleLabel = (row: SummaryRow) =>
  row.supplier_is_manufacturer === null
    ? "Не определено"
    : row.supplier_is_manufacturer
      ? "Да"
      : "Нет";

const containsQuotation = (row: SummaryRow, quotationId: number | null) =>
  quotationId !== null && row.quotation_ids.includes(quotationId);

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

interface SummaryEditDraft {
  supplier: string;
  supplier_type: "" | "manufacturer" | "distributor";
  price: string;
  currency: string;
  incoterm: string;
  moq: string;
  grade: string;
  payment_terms: string;
  lead_time: string;
  manufacturer: string;
  origin_country: string;
  packaging: string;
  price_unit: string;
  quoted_quantity: string;
  total_price: string;
  delivery_cost: string;
  duty_cost: string;
  vat_cost: string;
  landed_cost: string;
  cost_currency: string;
  is_hazmat: "unknown" | "yes" | "no";
  has_coa: boolean;
  has_tds: boolean;
}

type SummaryTextEditField = Exclude<
  keyof SummaryEditDraft,
  "has_coa" | "has_tds"
>;

const editDraftFromRow = (row: SummaryRow): SummaryEditDraft => ({
  supplier: row.supplier ?? "",
  supplier_type:
    row.supplier_is_manufacturer === null
      ? ""
      : row.supplier_is_manufacturer
        ? "manufacturer"
        : "distributor",
  price: row.price?.toString() ?? "",
  currency: row.currency ?? "",
  incoterm: row.incoterm ?? "",
  moq: row.moq ?? "",
  grade: row.grade ?? "",
  payment_terms: row.payment_terms ?? "",
  lead_time: row.lead_time ?? "",
  manufacturer: row.manufacturer ?? "",
  origin_country: row.origin_country ?? "",
  packaging: row.packaging ?? "",
  price_unit: row.price_unit ?? "",
  quoted_quantity: row.quoted_quantity ?? "",
  total_price: row.total_price?.toString() ?? "",
  delivery_cost: row.delivery_cost?.toString() ?? "",
  duty_cost: row.duty_cost?.toString() ?? "",
  vat_cost: row.vat_cost?.toString() ?? "",
  landed_cost: row.landed_cost?.toString() ?? "",
  cost_currency: row.cost_currency ?? row.currency ?? "",
  is_hazmat:
    row.is_hazmat === null ? "unknown" : row.is_hazmat ? "yes" : "no",
  has_coa: row.has_coa,
  has_tds: row.has_tds,
});

const optionalText = (value: string) => value.trim() || null;

const optionalNumber = (value: string, label: string): number | null => {
  const normalized = value.trim().replace(/\s/g, "").replace(",", ".");
  if (!normalized) return null;
  const parsed = Number(normalized);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label}: укажите неотрицательное число`);
  }
  return parsed;
};

const updateFromDraft = (draft: SummaryEditDraft): QuotationUpdate => ({
  price: optionalNumber(draft.price, "Цена"),
  currency: optionalText(draft.currency)?.toUpperCase() ?? null,
  incoterm: optionalText(draft.incoterm),
  moq: optionalText(draft.moq),
  grade: optionalText(draft.grade),
  payment_terms: optionalText(draft.payment_terms),
  lead_time: optionalText(draft.lead_time),
  manufacturer: optionalText(draft.manufacturer),
  origin_country: optionalText(draft.origin_country),
  packaging: optionalText(draft.packaging),
  price_unit: optionalText(draft.price_unit),
  quoted_quantity: optionalText(draft.quoted_quantity),
  total_price: optionalNumber(draft.total_price, "Стоимость закупки"),
  delivery_cost: optionalNumber(draft.delivery_cost, "Доставка"),
  duty_cost: optionalNumber(draft.duty_cost, "Пошлина"),
  vat_cost: optionalNumber(draft.vat_cost, "НДС"),
  landed_cost: optionalNumber(draft.landed_cost, "Итого до склада"),
  cost_currency: optionalText(draft.cost_currency)?.toUpperCase() ?? null,
  is_hazmat:
    draft.is_hazmat === "unknown" ? null : draft.is_hazmat === "yes",
  has_coa: draft.has_coa,
  has_tds: draft.has_tds,
});

type SummaryColumnKey =
  | "decision"
  | "supplier"
  | "manufacturer"
  | "country"
  | "packaging"
  | "grade"
  | "hazmat"
  | "price"
  | "price_unit"
  | "quantity"
  | "moq"
  | "purchase"
  | "delivery"
  | "duty"
  | "vat"
  | "landed"
  | "incoterm"
  | "payment"
  | "lead_time"
  | "documents"
  | "status";

interface SummaryColumnDefinition {
  key: SummaryColumnKey;
  label: string;
  width: number;
}

const SUMMARY_COLUMNS: SummaryColumnDefinition[] = [
  { key: "decision", label: "Выбор поставщика", width: 56 },
  { key: "supplier", label: "Поставщик", width: 190 },
  { key: "manufacturer", label: "Производитель", width: 130 },
  { key: "country", label: "Страна", width: 120 },
  { key: "packaging", label: "Фасовка", width: 150 },
  { key: "grade", label: "Грейд", width: 150 },
  { key: "hazmat", label: "HAZMAT", width: 90 },
  { key: "price", label: "Цена", width: 120 },
  { key: "price_unit", label: "Единица", width: 80 },
  { key: "quantity", label: "Объём", width: 110 },
  { key: "moq", label: "MOQ", width: 110 },
  { key: "purchase", label: "Закупка", width: 130 },
  { key: "delivery", label: "Доставка", width: 125 },
  { key: "duty", label: "Пошлина", width: 120 },
  { key: "vat", label: "НДС", width: 120 },
  { key: "landed", label: "Итого до склада", width: 145 },
  { key: "incoterm", label: "Incoterm", width: 90 },
  { key: "payment", label: "Оплата", width: 190 },
  { key: "lead_time", label: "Срок", width: 125 },
  { key: "documents", label: "Документы", width: 120 },
  { key: "status", label: "Статус", width: 100 },
];

const ALL_SUMMARY_COLUMN_KEYS = SUMMARY_COLUMNS.map(({ key }) => key);
const REQUIRED_WEBSITE_COLUMN_KEYS: SummaryColumnKey[] = ["decision"];
const WEBSITE_COLUMNS_STORAGE_KEY = "chemsource.summary.columns.website.v1";
const DOWNLOAD_COLUMNS_STORAGE_KEY = "chemsource.summary.columns.download.v1";

const readStoredColumns = (
  storageKey: string,
  requiredColumns: SummaryColumnKey[] = [],
): SummaryColumnKey[] => {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return [...ALL_SUMMARY_COLUMN_KEYS];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...ALL_SUMMARY_COLUMN_KEYS];
    const allowed = new Set(ALL_SUMMARY_COLUMN_KEYS);
    const selected = ALL_SUMMARY_COLUMN_KEYS.filter(
      (key) =>
        (parsed.includes(key) && allowed.has(key)) ||
        requiredColumns.includes(key),
    );
    return selected.length > 0 ? selected : [...ALL_SUMMARY_COLUMN_KEYS];
  } catch {
    return [...ALL_SUMMARY_COLUMN_KEYS];
  }
};

const csvCell = (value: string) => {
  // Ответы поставщика — недоверенный ввод. Excel не должен выполнить значение
  // ячейки как формулу, если оно начинается с =, +, - или @.
  const safeValue = /^\s*[=+\-@]/.test(value) ? `'${value}` : value;
  return `"${safeValue.replace(/"/g, '""')}"`;
};

const exportColumnValue = (
  column: SummaryColumnKey,
  row: SummaryRow,
  selectedQuotationId: number | null,
) => {
  switch (column) {
    case "decision":
      return containsQuotation(row, selectedQuotationId) ? "выбрано" : "";
    case "supplier":
      return supplierName(row);
    case "manufacturer":
      return manufacturerRoleLabel(row);
    case "country":
      return row.origin_country ?? "";
    case "packaging":
      return row.packaging ?? "";
    case "grade":
      return row.grade ?? "";
    case "hazmat":
      return row.is_hazmat === null ? "" : row.is_hazmat ? "да" : "нет";
    case "price":
      return row.price === null ? "" : formatPrice(row.price, row.currency);
    case "price_unit":
      return row.price_unit ?? "";
    case "quantity":
      return row.quoted_quantity ?? "";
    case "moq":
      return row.moq ?? "";
    case "purchase":
      return row.total_price === null
        ? ""
        : formatPrice(row.total_price, costCurrency(row));
    case "delivery":
      return row.delivery_cost === null
        ? ""
        : formatPrice(row.delivery_cost, costCurrency(row));
    case "duty":
      return row.duty_cost === null
        ? ""
        : formatPrice(row.duty_cost, costCurrency(row));
    case "vat":
      return row.vat_cost === null
        ? ""
        : formatPrice(row.vat_cost, costCurrency(row));
    case "landed":
      return row.landed_cost === null
        ? ""
        : formatPrice(row.landed_cost, costCurrency(row));
    case "incoterm":
      return row.incoterm ?? "";
    case "payment":
      return row.payment_terms ?? "";
    case "lead_time":
      return row.lead_time ?? "";
    case "documents":
      return documentsLabel(row);
    case "status":
      return row.is_complete ? "полная" : "неполная";
  }
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
  const [columnSettingsOpen, setColumnSettingsOpen] = useState(false);
  const [tableEditing, setTableEditing] = useState(false);
  const [editingQuotationId, setEditingQuotationId] = useState<number | null>(
    null,
  );
  const [editDraft, setEditDraft] = useState<SummaryEditDraft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [websiteColumns, setWebsiteColumns] = useState<SummaryColumnKey[]>(() =>
    readStoredColumns(
      WEBSITE_COLUMNS_STORAGE_KEY,
      REQUIRED_WEBSITE_COLUMN_KEYS,
    ),
  );
  const [downloadColumns, setDownloadColumns] = useState<SummaryColumnKey[]>(() =>
    readStoredColumns(DOWNLOAD_COLUMNS_STORAGE_KEY),
  );
  const columnSettingsTriggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    window.localStorage.setItem(
      WEBSITE_COLUMNS_STORAGE_KEY,
      JSON.stringify(websiteColumns),
    );
  }, [websiteColumns]);

  useEffect(() => {
    window.localStorage.setItem(
      DOWNLOAD_COLUMNS_STORAGE_KEY,
      JSON.stringify(downloadColumns),
    );
  }, [downloadColumns]);

  useEffect(() => {
    if (!columnSettingsOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setColumnSettingsOpen(false);
        columnSettingsTriggerRef.current?.focus();
      }
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [columnSettingsOpen]);

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
        setTableEditing(false);
        setEditingQuotationId(null);
        setEditDraft(null);
        setEditError(null);
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
    rows.find((row) => containsQuotation(row, selectedQuotationId)) ?? null;
  const editingRow =
    rows.find((row) => row.quotation_id === editingQuotationId) ?? null;
  const dialogueRow = selectedRow;
  const visibleWebsiteColumns = useMemo(
    () => SUMMARY_COLUMNS.filter(({ key }) => websiteColumns.includes(key)),
    [websiteColumns],
  );
  const selectedDownloadColumns = useMemo(
    () => SUMMARY_COLUMNS.filter(({ key }) => downloadColumns.includes(key)),
    [downloadColumns],
  );
  const visibleTableWidth = useMemo(
    () => visibleWebsiteColumns.reduce((total, column) => total + column.width, 0),
    [visibleWebsiteColumns],
  );
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
      containsQuotation(row, decision?.quotation_id ?? null)
        ? (decision?.note ?? "")
        : "",
    );
    setNotice(null);
  };

  const closeColumnSettings = () => {
    setColumnSettingsOpen(false);
    columnSettingsTriggerRef.current?.focus();
  };

  const cancelInlineEdit = () => {
    if (editSaving) return;
    setEditingQuotationId(null);
    setEditDraft(null);
    setEditError(null);
  };

  const startInlineEdit = (row: SummaryRow) => {
    if (readOnly || !tableEditing || editSaving) return;
    if (editingQuotationId === row.quotation_id) return;
    if (editingQuotationId !== null) {
      setEditError("Сначала сохраните или отмените изменения текущей строки.");
      return;
    }
    selectQuotation(row);
    setEditingQuotationId(row.quotation_id);
    setEditDraft(editDraftFromRow(row));
    setEditError(null);
    setNotice(null);
  };

  const toggleTableEditing = () => {
    if (readOnly || editSaving) return;
    if (tableEditing) {
      if (editingQuotationId !== null) {
        setEditError("Сохраните или отмените изменения строки перед выходом.");
        return;
      }
      setTableEditing(false);
      setEditError(null);
      return;
    }
    setTableEditing(true);
    setEditError(null);
    if (selectedRow) {
      setEditingQuotationId(selectedRow.quotation_id);
      setEditDraft(editDraftFromRow(selectedRow));
      setNotice(`Редактирование строки «${supplierName(selectedRow)}» включено.`);
    } else {
      setNotice("Нажмите на любую изменяемую ячейку, чтобы редактировать строку.");
    }
  };

  const changeEditField = <Key extends keyof SummaryEditDraft>(
    key: Key,
    value: SummaryEditDraft[Key],
  ) => {
    setEditDraft((current) =>
      current ? { ...current, [key]: value } : current,
    );
    setEditError(null);
  };

  const saveInlineChanges = async () => {
    if (!editingRow || !editDraft || readOnly) return;
    setEditSaving(true);
    setEditError(null);
    try {
      // Сначала валидируем все числовые и текстовые поля локально, чтобы при
      // ошибке котировки не успеть частично изменить карточку поставщика.
      const quotationChanges = updateFromDraft(editDraft);
      const supplier = optionalText(editDraft.supplier);
      if (editingRow.supplier_id !== null && !supplier) {
        throw new Error("Поставщик: название компании не может быть пустым");
      }
      if (editingRow.supplier_id !== null) {
        const currentType =
          editingRow.supplier_is_manufacturer === null
            ? ""
            : editingRow.supplier_is_manufacturer
              ? "manufacturer"
              : "distributor";
        const supplierChanges: {
          company?: string;
          type?: "manufacturer" | "distributor" | null;
        } = {};
        if (supplier !== editingRow.supplier) {
          supplierChanges.company = supplier ?? "";
        }
        if (editDraft.supplier_type !== currentType) {
          supplierChanges.type = editDraft.supplier_type || null;
        }
        if (Object.keys(supplierChanges).length > 0) {
          await api.updateSupplier(editingRow.supplier_id, supplierChanges);
        }
      }
      await api.updateQuotation(
        rfq.id,
        editingRow.quotation_id,
        quotationChanges,
      );
      const updatedRows = await api.getSummary(rfq.id);
      setRows(updatedRows);
      setEditingQuotationId(null);
      setEditDraft(null);
      setNotice(`Строка поставщика «${supplier ?? supplierName(editingRow)}» сохранена.`);
    } catch (caught) {
      setEditError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setEditSaving(false);
    }
  };

  const toggleColumn = (
    column: SummaryColumnKey,
    selected: SummaryColumnKey[],
    update: (columns: SummaryColumnKey[]) => void,
    requiredColumns: SummaryColumnKey[] = [],
  ) => {
    if (requiredColumns.includes(column)) return;
    if (selected.includes(column)) {
      if (selected.length === 1) return;
      update(selected.filter((key) => key !== column));
      return;
    }
    update(
      ALL_SUMMARY_COLUMN_KEYS.filter(
        (key) => key === column || selected.includes(key),
      ),
    );
  };

  const downloadSummary = () => {
    if (shown.length === 0 || selectedDownloadColumns.length === 0) return;
    const csvRows = [
      selectedDownloadColumns.map(({ label }) => csvCell(label)).join(";"),
      ...shown.map((row) =>
        selectedDownloadColumns
          .map(({ key }) =>
            csvCell(exportColumnValue(key, row, selectedQuotationId)),
          )
          .join(";"),
      ),
    ];
    const blob = new Blob(["\uFEFF", csvRows.join("\r\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `rfq-${rfq.id}-summary.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setNotice(
      `Таблица скачана: ${selectedDownloadColumns.length} столбцов, ${shown.length} предложений.`,
    );
  };

  const renderInlineEditor = (
    column: SummaryColumnKey,
    row: SummaryRow,
  ): ReactNode | null => {
    if (!editDraft || editingQuotationId !== row.quotation_id) return null;

    const textInput = (
      key: SummaryTextEditField,
      label: string,
      options: { maxLength?: number; decimal?: boolean; placeholder?: string } = {},
    ) => (
      <Input
        aria-label={label}
        className="summary-inline-input"
        inputMode={options.decimal ? "decimal" : undefined}
        maxLength={options.maxLength}
        placeholder={options.placeholder}
        value={editDraft[key]}
        onChange={(event) => changeEditField(key, event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            void saveInlineChanges();
          } else if (event.key === "Escape") {
            event.preventDefault();
            cancelInlineEdit();
          }
        }}
      />
    );

    const moneyInput = (key: SummaryTextEditField, label: string) => (
      <div className="summary-inline-stack">
        {textInput(key, label, { decimal: true, placeholder: "0,00" })}
        {textInput("cost_currency", `${label}: валюта`, {
          maxLength: 3,
          placeholder: "USD",
        })}
      </div>
    );

    switch (column) {
      case "supplier":
        return row.supplier_id === null
          ? <span className="summary-inline-readonly">{supplierName(row)}</span>
          : textInput("supplier", "Поставщик", { maxLength: 255 });
      case "manufacturer":
        return row.supplier_id === null ? (
          <span className="summary-inline-readonly">Не определено</span>
        ) : (
          <select
            aria-label="Производитель"
            className="ui-control summary-inline-input"
            value={editDraft.supplier_type}
            onChange={(event) =>
              changeEditField(
                "supplier_type",
                event.target.value as SummaryEditDraft["supplier_type"],
              )
            }
          >
            <option value="">Не определено</option>
            <option value="manufacturer">Да</option>
            <option value="distributor">Нет</option>
          </select>
        );
      case "country":
        return textInput("origin_country", "Страна", { maxLength: 120 });
      case "packaging":
        return textInput("packaging", "Фасовка", { maxLength: 255 });
      case "grade":
        return textInput("grade", "Грейд", { maxLength: 120 });
      case "hazmat":
        return (
          <select
            aria-label="HAZMAT"
            className="ui-control summary-inline-input"
            value={editDraft.is_hazmat}
            onChange={(event) =>
              changeEditField(
                "is_hazmat",
                event.target.value as SummaryEditDraft["is_hazmat"],
              )
            }
          >
            <option value="unknown">Не указано</option>
            <option value="yes">Да</option>
            <option value="no">Нет</option>
          </select>
        );
      case "price":
        return (
          <div className="summary-inline-stack">
            {textInput("price", "Цена", { decimal: true, placeholder: "0,00" })}
            {textInput("currency", "Валюта цены", {
              maxLength: 3,
              placeholder: "USD",
            })}
          </div>
        );
      case "price_unit":
        return textInput("price_unit", "Единица цены", {
          maxLength: 32,
          placeholder: "kg",
        });
      case "quantity":
        return textInput("quoted_quantity", "Предложенный объём", {
          maxLength: 64,
        });
      case "moq":
        return textInput("moq", "MOQ", { maxLength: 64 });
      case "purchase":
        return moneyInput("total_price", "Стоимость закупки");
      case "delivery":
        return moneyInput("delivery_cost", "Доставка");
      case "duty":
        return moneyInput("duty_cost", "Пошлина");
      case "vat":
        return moneyInput("vat_cost", "НДС");
      case "landed":
        return moneyInput("landed_cost", "Итого до склада");
      case "incoterm":
        return textInput("incoterm", "Incoterm", { maxLength: 8 });
      case "payment":
        return textInput("payment_terms", "Условия оплаты", { maxLength: 255 });
      case "lead_time":
        return textInput("lead_time", "Срок поставки", { maxLength: 120 });
      case "documents":
        return (
          <div className="summary-inline-checks">
            <label>
              <input
                checked={editDraft.has_coa}
                type="checkbox"
                onChange={(event) =>
                  changeEditField("has_coa", event.target.checked)
                }
              />
              CoA
            </label>
            <label>
              <input
                checked={editDraft.has_tds}
                type="checkbox"
                onChange={(event) =>
                  changeEditField("has_tds", event.target.checked)
                }
              />
              TDS
            </label>
          </div>
        );
      case "decision":
      case "status":
        return null;
    }
  };

  const renderTableCell = (
    column: SummaryColumnKey,
    row: SummaryRow,
  ): ReactNode => {
    const editor = renderInlineEditor(column, row);
    if (editor !== null) return editor;
    switch (column) {
      case "decision":
        return (
          <input
            aria-label={`Выбрать предложение ${supplierName(row)}`}
            checked={containsQuotation(row, selectedQuotationId)}
            disabled={readOnly}
            name="purchase-decision"
            onChange={() => selectQuotation(row)}
            type="radio"
          />
        );
      case "supplier":
        return (
          <button
            aria-pressed={containsQuotation(row, selectedQuotationId)}
            className="summary-supplier-button"
            onClick={() => {
              if (tableEditing) startInlineEdit(row);
              else selectQuotation(row);
            }}
            type="button"
          >
            <strong>{supplierName(row)}</strong>
          </button>
        );
      case "manufacturer":
        return manufacturerRoleLabel(row);
      case "country":
        return row.origin_country ?? "—";
      case "packaging":
        return row.packaging ?? "—";
      case "grade":
        return row.grade ?? "—";
      case "hazmat":
        return row.is_hazmat === null ? "—" : row.is_hazmat ? "да" : "нет";
      case "price":
        return pricePerUnit(row);
      case "price_unit":
        return row.price_unit ?? "—";
      case "quantity":
        return row.quoted_quantity ?? "—";
      case "moq":
        return row.moq ?? "—";
      case "purchase":
        return formatPrice(row.total_price, costCurrency(row));
      case "delivery":
        return formatPrice(row.delivery_cost, costCurrency(row));
      case "duty":
        return formatPrice(row.duty_cost, costCurrency(row));
      case "vat":
        return formatPrice(row.vat_cost, costCurrency(row));
      case "landed":
        return formatPrice(row.landed_cost, costCurrency(row));
      case "incoterm":
        return row.incoterm ?? "—";
      case "payment":
        return row.payment_terms ?? "—";
      case "lead_time":
        return row.lead_time ?? "—";
      case "documents":
        return documentsLabel(row);
      case "status":
        return (
          <span className={`badge ${row.is_complete ? "tone-ok" : "tone-warn"}`}>
            {row.is_complete ? "полная" : "неполная"}
          </span>
        );
    }
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
          <div className="summary-heading-actions">
            <label className="summary-complete-filter">
              <input
                checked={onlyComplete}
                onChange={(event) => setOnlyComplete(event.target.checked)}
                type="checkbox"
              />
              Только полные
            </label>
            {!readOnly && (
              <button
                aria-pressed={tableEditing}
                className="secondary"
                type="button"
                onClick={toggleTableEditing}
              >
                {tableEditing
                  ? "Завершить редактирование"
                  : "Редактировать таблицу"}
              </button>
            )}
            <button
              className="secondary"
              disabled={shown.length === 0}
              type="button"
              onClick={downloadSummary}
            >
              Скачать CSV
            </button>
            <button
              aria-haspopup="dialog"
              className="secondary"
              ref={columnSettingsTriggerRef}
              type="button"
              onClick={() => setColumnSettingsOpen(true)}
            >
              Настроить столбцы
            </button>
          </div>
        </div>

        {error && <p className="error">{error}</p>}
        {notice && <p className="success-note">{notice}</p>}
        {tableEditing && (
          <div className="summary-inline-toolbar" role="status">
            <span>
              {editingRow
                ? `Редактируется: ${supplierName(editingRow)}`
                : "Нажмите на любую изменяемую ячейку таблицы."}
            </span>
            {editingRow && (
              <div className="summary-inline-actions">
                <button
                  className="secondary"
                  disabled={editSaving}
                  type="button"
                  onClick={cancelInlineEdit}
                >
                  Отменить
                </button>
                <button
                  disabled={editSaving}
                  type="button"
                  onClick={() => void saveInlineChanges()}
                >
                  {editSaving ? "Сохраняем…" : "Сохранить строку"}
                </button>
              </div>
            )}
          </div>
        )}
        {editError && <p className="error">{editError}</p>}
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
                <table
                  className="summary summary-detailed-table"
                  style={{
                    minWidth: `${visibleTableWidth}px`,
                    width: `${visibleTableWidth}px`,
                  }}
                >
                  <colgroup>
                    {visibleWebsiteColumns.map((column) => (
                      <col key={column.key} style={{ width: `${column.width}px` }} />
                    ))}
                  </colgroup>
                  <thead>
                    <tr>
                      {visibleWebsiteColumns.map((column) => (
                        <th
                          aria-label={
                            column.key === "decision" ? column.label : undefined
                          }
                          key={column.key}
                        >
                          {column.key === "decision" ? null : column.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((row) => (
                      <tr
                        className={`${row.is_complete ? "" : "incomplete"} ${
                          containsQuotation(row, selectedQuotationId)
                            ? "summary-selected-row"
                            : ""
                        }`}
                        key={row.quotation_id}
                      >
                        {visibleWebsiteColumns.map((column) => (
                          <td
                            className={
                              tableEditing &&
                              column.key !== "decision" &&
                              column.key !== "status"
                                ? "summary-editable-cell"
                                : undefined
                            }
                            key={column.key}
                            data-column={column.key}
                            tabIndex={
                              tableEditing &&
                              column.key !== "decision" &&
                              column.key !== "status"
                                ? 0
                                : undefined
                            }
                            onClick={() => {
                              if (
                                tableEditing &&
                                column.key !== "decision" &&
                                column.key !== "status"
                              ) {
                                startInlineEdit(row);
                              }
                            }}
                            onKeyDown={(event) => {
                              if (
                                event.key === "Enter" &&
                                tableEditing &&
                                column.key !== "decision" &&
                                column.key !== "status"
                              ) {
                                event.preventDefault();
                                startInlineEdit(row);
                              }
                            }}
                          >
                            {renderTableCell(column.key, row)}
                          </td>
                        ))}
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
              <div><span>Производитель</span><strong>{manufacturerRoleLabel(selectedRow)}</strong></div>
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

      {columnSettingsOpen && (
        <div
          className="summary-columns-backdrop"
          role="presentation"
          onClick={closeColumnSettings}
        >
          <section
            aria-describedby="summary-columns-description"
            aria-labelledby="summary-columns-title"
            aria-modal="true"
            className="summary-columns-dialog"
            role="dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="summary-columns-dialog-header">
              <div>
                <h2 id="summary-columns-title">Настройка столбцов</h2>
                <p className="note" id="summary-columns-description">
                  Отдельно выберите данные для экрана и для скачиваемого CSV.
                  Настройки сохраняются в этом браузере.
                </p>
              </div>
              <IconButton
                autoFocus
                icon="close"
                label="Закрыть настройку столбцов"
                onClick={closeColumnSettings}
              />
            </header>

            <div className="summary-columns-sections">
              <fieldset className="summary-columns-section">
                <legend>На сайте</legend>
                <div className="summary-columns-section-heading">
                  <span className="note">
                    Выбрано: {websiteColumns.length} из {SUMMARY_COLUMNS.length}
                  </span>
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => setWebsiteColumns([...ALL_SUMMARY_COLUMN_KEYS])}
                  >
                    Выбрать все
                  </button>
                </div>
                <div className="summary-columns-checklist">
                  {SUMMARY_COLUMNS.map((column) => (
                    <label key={column.key}>
                      <input
                        checked={websiteColumns.includes(column.key)}
                        disabled={REQUIRED_WEBSITE_COLUMN_KEYS.includes(
                          column.key,
                        )}
                        type="checkbox"
                        onChange={() =>
                          toggleColumn(
                            column.key,
                            websiteColumns,
                            setWebsiteColumns,
                            REQUIRED_WEBSITE_COLUMN_KEYS,
                          )
                        }
                      />
                      <span>
                        {column.label}
                        {REQUIRED_WEBSITE_COLUMN_KEYS.includes(column.key)
                          ? " · всегда отображается"
                          : ""}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="summary-columns-section">
                <legend>При скачивании таблицы</legend>
                <div className="summary-columns-section-heading">
                  <span className="note">
                    Выбрано: {downloadColumns.length} из {SUMMARY_COLUMNS.length}
                  </span>
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => setDownloadColumns([...ALL_SUMMARY_COLUMN_KEYS])}
                  >
                    Выбрать все
                  </button>
                </div>
                <div className="summary-columns-checklist">
                  {SUMMARY_COLUMNS.map((column) => (
                    <label key={column.key}>
                      <input
                        checked={downloadColumns.includes(column.key)}
                        type="checkbox"
                        onChange={() =>
                          toggleColumn(
                            column.key,
                            downloadColumns,
                            setDownloadColumns,
                          )
                        }
                      />
                      <span>{column.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>

            <footer className="summary-columns-dialog-footer">
              <span className="note">
                Нельзя скрыть последний столбец. Скачивание учитывает фильтр
                «Только полные».
              </span>
              <button type="button" onClick={closeColumnSettings}>
                Готово
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
