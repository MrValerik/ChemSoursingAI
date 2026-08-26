// Тонкий HTTP-клиент к бэкенду. В dev запросы идут через Vite-прокси (/api -> :8000).
// JWT-токен хранится в localStorage и добавляется в Authorization.

import type {
  AnalogVariation,
  ChannelStatus,
  RfqBatchCreateResult,
  RfqBatchSummary,
  RfqImportPreview,
  RfqImportRow,
  CommunicationMessageRead,
  CommunicationOverviewRead,
  CommunicationTranslationRead,
  IdentificationMethod,
  EmailIntegration,
  EmailSyncRead,
  FeedbackMessage,
  EscalationRead,
  PriceHistoryItem,
  PromptRead,
  PromptVersionRead,
  RecipientRead,
  SearchRunReplay,
  SupplierDocumentDetail,
  SupplierDocumentRead,
  SearchRunListItem,
  SupplierRead,
  IntermediaryRead,
  IntermediaryKind,
  SearchScope,
  SupplierQualificationResponse,
  SupplierSearchJob,
  SupplierSearchResponse,
  SearchRunTrace,
  TemplateRead,
  IntegrationConnectionResult,
  CommunicationTestRun,
  UserAdminRead,
  UserRead,
  WhatsAppIntegration,
  WhatsAppWebQr,
  WhatsAppWebPairingCode,
  WhatsAppWebStatus,
  ExtractedQuote,
  QuotationRead,
  RFQListItem,
  RFQPreview,
  RFQRead,
  RfqAiSetting,
  SubstanceInfo,
  SubstanceResolution,
  SubstanceHistoryEntry,
  SubstanceRecord,
  SummaryRow,
  PurchaseDecisionRead,
  TokenResponse,
} from "./types";
import {
  apiResponseErrorMessage,
  userErrorMessage,
} from "./errors";

export { userErrorMessage } from "./errors";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const TOKEN_KEY = "chemsource_token";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public searchRunId: number | null = null,
  ) {
    super(message);
    this.name = "Ошибка";
  }
}

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, { headers, ...options });
  } catch (error) {
    throw new ApiError(
      0,
      userErrorMessage(
        error,
        "Не удалось связаться с сервером. Проверьте подключение и повторите попытку.",
      ),
    );
  }
  if (!resp.ok) {
    if (resp.status === 401 && !path.startsWith("/auth/login")) {
      onUnauthorized?.();
    }
    let detail: unknown = null;
    let searchRunId: number | null = null;
    try {
      const data = await resp.json();
      const errorDetail = (data as {
        detail?: unknown;
      }).detail;
      detail = errorDetail;
      if (
        errorDetail &&
        typeof errorDetail === "object" &&
        !Array.isArray(errorDetail)
      ) {
        const structuredDetail = errorDetail as {
          message?: unknown;
          search_run_id?: unknown;
        };
        detail = structuredDetail.message ?? errorDetail;
        searchRunId =
          typeof structuredDetail.search_run_id === "number"
            ? structuredDetail.search_run_id
            : null;
      }
    } catch {
      detail = null;
    }
    throw new ApiError(
      resp.status,
      apiResponseErrorMessage(resp.status, detail, path),
      searchRunId,
    );
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  try {
    return (await resp.json()) as T;
  } catch {
    throw new ApiError(
      502,
      "Сервер вернул некорректный ответ. Обновите страницу и повторите попытку.",
    );
  }
}

// Загрузка файла. Отдельно от request: тот жёстко ставит
// Content-Type: application/json, а для multipart заголовок обязан
// проставить браузер — он же вписывает в него границу частей.
async function requestUpload<T>(path: string, file: File): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const body = new FormData();
  body.append("file", file);

  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, { method: "POST", headers, body });
  } catch (error) {
    throw new ApiError(
      0,
      userErrorMessage(
        error,
        "Не удалось связаться с сервером. Проверьте подключение и повторите попытку.",
      ),
    );
  }
  if (!resp.ok) {
    if (resp.status === 401) onUnauthorized?.();
    let detail: unknown = null;
    try {
      detail = ((await resp.json()) as { detail?: unknown }).detail;
    } catch {
      detail = null;
    }
    throw new ApiError(resp.status, apiResponseErrorMessage(resp.status, detail, path));
  }
  return (await resp.json()) as T;
}

async function requestFile(path: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { headers });
  } catch (error) {
    throw new ApiError(
      0,
      userErrorMessage(
        error,
        "Не удалось скачать файл. Проверьте подключение и повторите попытку.",
      ),
    );
  }
  if (!response.ok) {
    if (response.status === 401) onUnauthorized?.();
    throw new ApiError(
      response.status,
      response.status === 410
        ? "Файл больше недоступен в хранилище."
        : "Не удалось скачать вложение.",
    );
  }
  return response.blob();
}

export interface RFQCreatePayload {
  identification_method?: IdentificationMethod;
  /** Необязателен: у смесей и промышленных продуктов номера нет. */
  cas?: string | null;
  name: string;
  analog_reference?: string | null;
  analog_variations?: AnalogVariation[];
  specification?: string | null;
  /** Названия, отмеченные закупщиком, и снятые им. */
  confirmed_synonyms?: string[];
  excluded_names?: string[];
  incoterms: string[];
  channels?: string[];
  search_countries: string[];
  supplier_target: number;
  substance_id?: number | null;
  additional_instructions?: string | null;
  purity?: string | null;
  application?: string | null;
  volume?: string | null;
  target_price?: number | null;
  currency?: string;
  /** Внутренняя заметка закупщика: в письмо поставщику не уходит. */
  specialist_comment?: string | null;
}

export const api = {
  // --- Аутентификация ---
  login: (username: string, password: string) =>
    request<TokenResponse>(`/auth/login`, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  me: () => request<UserRead>(`/auth/me`),

  // --- Вещества и RFQ ---
  verifyCas: (cas: string) =>
    request<SubstanceInfo>(`/substances/verify?cas=${encodeURIComponent(cas)}`),

  // Обратная сторона verifyCas: номера нет, есть только название. Именно так
  // позиции и приходят от заказчика — списком названий.
  createRfqBatch: (payload: {
    idempotency_key: string;
    source_name?: string | null;
    items: { row: number; values: Record<string, unknown> }[];
  }) =>
    request<RfqBatchCreateResult>("/rfq/batch", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getRfqBatch: (batchId: number) =>
    request<RfqBatchSummary>(`/rfq/batch/${batchId}`),
  previewRfqImport: (file: File) =>
    requestUpload<RfqImportPreview>("/rfq/import/preview", file),
  recheckRfqImportRow: (row: number, raw: Record<string, string>) =>
    request<RfqImportRow>("/rfq/import/row", {
      method: "POST",
      body: JSON.stringify({ row, raw }),
    }),
  resolveSubstance: (name: string) =>
    request<SubstanceResolution>(`/substances/resolve`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  listSubstances: (query = "") =>
    request<SubstanceRecord[]>(
      `/substances${query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ""}`,
    ),

  createSubstance: (payload: {
    cas: string;
    preferred_name: string;
    synonyms?: string[];
    excluded_names?: string[];
    notes?: string | null;
  }) =>
    request<SubstanceRecord>(`/substances`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateSubstance: (
    id: number,
    payload: {
      preferred_name?: string;
      synonyms?: string[];
      excluded_names?: string[];
      notes?: string | null;
    },
  ) =>
    request<SubstanceRecord>(`/substances/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  listSubstanceHistory: (id: number) =>
    request<SubstanceHistoryEntry[]>(`/substances/${id}/history`),

  decideSubstanceIdentity: (
    rfqId: number,
    payload: {
      action: "confirm" | "reject";
      suggested_name: string;
      preferred_name?: string | null;
      synonyms?: string[];
      note?: string | null;
      verification?: Record<string, unknown> | null;
    },
  ) =>
    request<SubstanceRecord>(`/substances/rfq/${rfqId}/decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  previewRfq: (payload: RFQCreatePayload) =>
    request<RFQPreview>(`/rfq/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  createRfq: (
    payload: RFQCreatePayload,
    verify = true,
    startSearch = false,
  ) =>
    request<RFQRead>(
      `/rfq?verify=${verify}&start_search=${startSearch}`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),

  getRfq: (id: number) => request<RFQRead>(`/rfq/${id}`),

  listRfqs: () => request<RFQListItem[]>(`/rfq`),

  deleteRfq: (id: number) =>
    request<void>(`/rfq/${id}`, { method: "DELETE" }),

  extractQuote: (
    text: string,
    useLlm = true,
    rfqId?: number,
    additionalInstructions?: string,
  ) =>
    request<ExtractedQuote>(`/extraction/quote`, {
      method: "POST",
      body: JSON.stringify({
        text,
        use_llm: useLlm,
        rfq_id: rfqId,
        additional_instructions: additionalInstructions,
      }),
    }),

  extractAndStore: (
    rfqId: number,
    text: string,
    useLlm = true,
    additionalInstructions?: string,
  ) =>
    request<QuotationRead>(`/rfq/${rfqId}/extract`, {
      method: "POST",
      body: JSON.stringify({
        text,
        use_llm: useLlm,
        additional_instructions: additionalInstructions,
      }),
    }),

  listQuotations: (rfqId: number) =>
    request<QuotationRead[]>(`/rfq/${rfqId}/quotations`),

  getSummary: (rfqId: number) =>
    request<SummaryRow[]>(`/rfq/${rfqId}/summary`),

  getPurchaseDecision: (rfqId: number) =>
    request<PurchaseDecisionRead | null>(`/rfq/${rfqId}/purchase-decision`),

  savePurchaseDecision: (
    rfqId: number,
    payload: { quotation_id: number; note: string | null },
  ) =>
    request<PurchaseDecisionRead>(`/rfq/${rfqId}/purchase-decision`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  listEscalations: (rfqId: number) =>
    request<EscalationRead[]>(`/rfq/${rfqId}/escalations`),

  escalateRfq: (rfqId: number, reason: string, note: string | null) =>
    request<EscalationRead>(`/rfq/${rfqId}/escalate`, {
      method: "POST",
      body: JSON.stringify({ reason, note }),
    }),

  priceHistory: (cas: string) =>
    request<PriceHistoryItem[]>(
      `/substances/price-history?cas=${encodeURIComponent(cas)}`,
    ),

  listSuppliers: () => request<SupplierRead[]>(`/suppliers`),

  listIntermediaries: () => request<IntermediaryRead[]>(`/intermediaries`),
  createIntermediary: (body: {
    domain: string;
    name: string;
    kind: IntermediaryKind;
    notes?: string | null;
  }) =>
    request<IntermediaryRead>(`/intermediaries`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateIntermediary: (id: number, body: Partial<IntermediaryRead>) =>
    request<IntermediaryRead>(`/intermediaries/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteIntermediary: (id: number) =>
    request<void>(`/intermediaries/${id}`, { method: "DELETE" }),

  addSupplier: (
    payload: {
      company: string;
      type?: string | null;
      country?: string | null;
      email?: string | null;
      whatsapp?: string | null;
      source?: string | null;
      reputation?: string | null;
      qualification_status?: string;
      evidence_score?: number | null;
      certificates?: string[] | null;
    },
    rfqId?: number,
    searchRunId?: number,
  ) => {
    const params = new URLSearchParams();
    if (rfqId !== undefined) params.set("rfq_id", String(rfqId));
    if (searchRunId !== undefined) {
      params.set("search_run_id", String(searchRunId));
    }
    const query = params.size > 0 ? `?${params.toString()}` : "";
    return request<SupplierRead>(`/suppliers${query}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateSupplier: (
    supplierId: number,
    payload: {
      company?: string;
      type?: "manufacturer" | "distributor" | null;
      country?: string | null;
      source?: string | null;
      reputation?: string | null;
      qualification_status?: "candidate" | "under_review" | "verified" | "rejected";
      evidence_score?: number | null;
      certificates?: string[] | null;
    },
  ) =>
    request<SupplierRead>(`/suppliers/${supplierId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteSupplier: (supplierId: number) =>
    request<void>(`/suppliers/${supplierId}`, { method: "DELETE" }),

  // Контакт, вписанный закупщиком с сайта компании: поиск на скрытом
  // адресе, форме обратной связи или странице площадки останавливается, а
  // человек эти преграды проходит.
  // Решение человека о компании: подтвердить, отправить на проверку,
  // вернуть в кандидаты или исключить из реестра.
  setSupplierQualification: (
    supplierId: number,
    status: "candidate" | "under_review" | "verified" | "rejected",
  ) =>
    request<SupplierRead>(`/suppliers/${supplierId}/qualification`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  // Отказ в рамках одного запроса: реестр не трогает.
  setSupplierExclusion: (rfqId: number, supplierId: number, excluded: boolean) =>
    request<SupplierRead>(
      `/rfq/${rfqId}/suppliers/${supplierId}/exclusion`,
      { method: "POST", body: JSON.stringify({ excluded }) },
    ),

  addSupplierContact: (
    supplierId: number,
    payload: {
      full_name?: string | null;
      email?: string | null;
      whatsapp?: string | null;
    },
    rfqId?: number,
  ) => {
    const query = rfqId === undefined ? "" : `?rfq_id=${rfqId}`;
    return request<SupplierRead>(`/suppliers/${supplierId}/contacts${query}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  removeSupplierContact: (supplierId: number, contactId: number) =>
    request<SupplierRead>(`/suppliers/${supplierId}/contacts/${contactId}`, {
      method: "DELETE",
    }),

  // Обратная связь: чего не хватает пользователю и что ему непонятно.
  sendFeedback: (payload: { text: string; origin?: string | null }) =>
    request<FeedbackMessage>(`/feedback`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listFeedback: () => request<FeedbackMessage[]>(`/feedback`),

  listRecipients: (rfqId: number) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/recipients`),

  updateRfqMessageDraft: (
    rfqId: number,
    payload: { subject: string | null; body: string | null },
  ) =>
    request<RFQRead>(`/rfq/${rfqId}/message-draft`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  selectRecipients: (rfqId: number, items: { supplier_id: number; channel: string }[]) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/recipients`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),

  dispatchRfq: (rfqId: number, confirmExternalSend = false) =>
    request<RecipientRead[]>(
      `/rfq/${rfqId}/dispatch?confirm_external_send=${confirmExternalSend}`,
      { method: "POST" },
    ),

  removeRecipient: (rfqId: number, recipientId: number) =>
    request<void>(`/rfq/${rfqId}/recipients/${recipientId}`, { method: "DELETE" }),

  communicationOverview: (rfqId: number) =>
    request<CommunicationOverviewRead>(`/rfq/${rfqId}/communications`),

  translateCommunicationDialogue: (rfqId: number, messageIds: number[]) =>
    request<CommunicationTranslationRead>(
      `/rfq/${rfqId}/communications/translation`,
      {
        method: "POST",
        body: JSON.stringify({ message_ids: messageIds }),
      },
    ),

  sendCommunicationMessage: (
    rfqId: number,
    payload: {
      manager_id: number;
      channel: "email" | "whatsapp";
      body: string;
      subject?: string | null;
      idempotency_key: string;
      confirm_external_send: boolean;
    },
  ) =>
    request<CommunicationMessageRead>(`/rfq/${rfqId}/communications/send`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  sendCommunicationDraft: (communicationId: number) =>
    request<CommunicationMessageRead>(`/communications/${communicationId}/send`, {
      method: "POST",
      body: JSON.stringify({ confirm_external_send: true }),
    }),

  syncEmailCommunications: (limit = 20) =>
    request<EmailSyncRead>(`/communications/email/sync?limit=${limit}`, {
      method: "POST",
    }),

  listEscalationQueue: () => request<EscalationRead[]>(`/escalations`),

  updateEscalation: (
    id: number,
    payload: { assignee?: string; status?: string; note?: string },
  ) =>
    request<EscalationRead>(`/escalations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  listUsers: () => request<UserAdminRead[]>(`/users`),

  createUser: (payload: {
    username: string;
    full_name: string;
    password: string;
    role: string;
  }) =>
    request<UserAdminRead>(`/users`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateUser: (
    id: number,
    payload: {
      full_name?: string;
      role?: string;
      is_active?: boolean;
      password?: string;
    },
  ) =>
    request<UserAdminRead>(`/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  channelsStatus: () => request<ChannelStatus[]>(`/settings/channels`),

  getEmailIntegration: () =>
    request<EmailIntegration>(`/settings/integrations/email`),

  updateEmailIntegration: (payload: {
    enabled: boolean;
    delivery_mode: "demo" | "live";
    email_from: string;
    email_from_name: string;
    email_timeout_s: number;
    auto_followup_mode: "off" | "draft" | "send";
    smtp_host: string;
    smtp_port: number;
    smtp_user: string;
    smtp_password?: string | null;
    smtp_use_ssl: boolean;
    smtp_starttls: boolean;
    imap_host: string;
    imap_port: number;
    imap_user: string;
    imap_password?: string | null;
    imap_use_ssl: boolean;
    imap_folder: string;
    clear_secrets?: boolean;
  }) =>
    request<EmailIntegration>(`/settings/integrations/email`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  checkEmailIntegration: () =>
    request<IntegrationConnectionResult>(`/settings/integrations/email/check`, {
      method: "POST",
    }),

  getWhatsAppIntegration: () =>
    request<WhatsAppIntegration>(`/settings/integrations/whatsapp`),

  updateWhatsAppIntegration: (payload: {
    enabled: boolean;
    transport: "cloud_api" | "web";
    phone_id: string;
    access_token?: string | null;
    api_base_url: string;
    api_version: string;
    timeout_s: number;
    clear_token?: boolean;
  }) =>
    request<WhatsAppIntegration>(`/settings/integrations/whatsapp`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  checkWhatsAppIntegration: () =>
    request<IntegrationConnectionResult>(
      `/settings/integrations/whatsapp/check`,
      { method: "POST" },
    ),

  getWhatsAppWebStatus: () =>
    request<WhatsAppWebStatus>(`/settings/integrations/whatsapp/web/status`),

  connectWhatsAppWeb: () =>
    request<WhatsAppWebStatus>(`/settings/integrations/whatsapp/web/connect`, {
      method: "POST",
    }),

  getWhatsAppWebQr: () =>
    request<WhatsAppWebQr>(`/settings/integrations/whatsapp/web/qr`),

  createWhatsAppWebPairingCode: (phoneNumber: string) =>
    request<WhatsAppWebPairingCode>(
      `/settings/integrations/whatsapp/web/pairing-code`,
      {
        method: "POST",
        body: JSON.stringify({ phone_number: phoneNumber }),
      },
    ),

  cancelWhatsAppWebPairingCode: () =>
    request<WhatsAppWebStatus>(
      `/settings/integrations/whatsapp/web/pairing-code/cancel`,
      { method: "POST" },
    ),

  disconnectWhatsAppWeb: () =>
    request<WhatsAppWebStatus>(
      `/settings/integrations/whatsapp/web/disconnect`,
      { method: "POST" },
    ),

  listCommunicationTests: (limit = 30, rfqId?: number) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (rfqId !== undefined) params.set("rfq_id", String(rfqId));
    return request<CommunicationTestRun[]>(
      `/communication-testing?${params.toString()}`,
    );
  },

  runCommunicationTest: (payload: {
    rfq_id?: number;
    channel: "email" | "whatsapp";
    recipient: string;
    procurement_context: string;
    additional_instructions: string;
    simulation_mode: "buyer_ai" | "supplier_ai";
    initial_message: string;
    delivery_mode: "preview" | "send";
    subject: string;
    confirm_external_send: boolean;
  }) =>
    request<CommunicationTestRun>(`/communication-testing`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  continueCommunicationTest: (
    runId: number,
    payload: {
      message: string;
      recipient: string;
      confirm_external_send: boolean;
      continue_after_complete: boolean;
    },
  ) =>
    request<CommunicationTestRun>(
      `/communication-testing/${runId}/messages`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),

  addCommunicationTestDemoDocument: (runId: number) =>
    request<CommunicationTestRun>(
      `/communication-testing/${runId}/demo-document-reply`,
      { method: "POST" },
    ),

  answerCommunicationTestEscalation: (runId: number, message: string) =>
    request<CommunicationTestRun>(
      `/communication-testing/${runId}/escalation-reply`,
      {
        method: "POST",
        body: JSON.stringify({ message }),
      },
    ),

  translateCommunicationTestDialogue: (runId: number) =>
    request<CommunicationTestRun>(`/communication-testing/${runId}/translation`, {
      method: "POST",
    }),

  translateRfqPreview: (rfqId: number) =>
    request<{ translation_ru: string }>(`/rfq/${rfqId}/translation`, {
      method: "POST",
    }),

  listTemplates: () => request<TemplateRead[]>(`/templates`),

  createTemplate: (payload: { kind: string; name: string; body: string }) =>
    request<TemplateRead>(`/templates`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateTemplate: (
    id: number,
    payload: { name?: string; body?: string; moderation?: string },
  ) =>
    request<TemplateRead>(`/templates/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  listPrompts: () => request<PromptRead[]>(`/prompts`),

  searchSuppliers: (payload: {
    cas: string | null;
    name: string;
    country?: string | null;
    additional_instructions?: string | null;
    limit?: number;
  }) =>
    request<SupplierSearchResponse>(`/supplier-search`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  enqueueSupplierSearch: (
    rfqId: number,
    payload: {
      // Поиск без номера ведётся по подтверждённым названиям.
      cas: string | null;
      name: string;
      country?: string | null;
      additional_instructions?: string | null;
      limit?: number;
      search_scope?: SearchScope;
    },
  ) =>
    request<SupplierSearchJob>(`/supplier-search/jobs?rfq_id=${rfqId}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listSearchRuns: (limit = 50, rfqId?: number) =>
    request<SearchRunListItem[]>(
      `/search-runs?limit=${limit}${rfqId === undefined ? "" : `&rfq_id=${rfqId}`}`,
    ),

  qualifySuppliers: (payload: {
    search_run_id?: number;
    cas: string | null;
    name: string;
    country?: string | null;
    additional_instructions?: string | null;
    results: SupplierSearchResponse["results"];
  }) =>
    request<SupplierQualificationResponse>(`/supplier-search/qualify`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSearchRun: (id: number, mergeCountry = true) =>
    request<SearchRunTrace>(
      `/search-runs/${id}${mergeCountry ? "?merge_country=true" : ""}`,
    ),

  restartSearchRun: (id: number) =>
    request<SupplierSearchJob>(`/search-runs/${id}/restart`, {
      method: "POST",
    }),

  listRfqDocuments: (rfqId: number) =>
    request<SupplierDocumentRead[]>(`/rfq/${rfqId}/documents`),

  downloadDocument: (id: number) => requestFile(`/documents/${id}/file`),

  verifyDocument: (id: number) =>
    request<SupplierDocumentDetail>(`/documents/${id}/verify`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  resumeSearchRun: (id: number) =>
    request<SupplierSearchJob>(`/search-runs/${id}/resume`, {
      method: "POST",
    }),

  replaySearchRunValidators: (id: number) =>
    request<SearchRunReplay>(`/search-runs/${id}/replay`, {
      method: "POST",
      body: JSON.stringify({ mode: "validator" }),
    }),

  createPrompt: (payload: {
    kind: string;
    name: string;
    description?: string | null;
    system_prompt: string;
  }) =>
    request<PromptRead>(`/prompts`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updatePrompt: (
    id: number,
    payload: {
      name?: string;
      description?: string | null;
      system_prompt?: string;
      is_active?: boolean;
    },
  ) =>
    request<PromptRead>(`/prompts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  promptVersions: (id: number) =>
    request<PromptVersionRead[]>(`/prompts/${id}/versions`),

  previewPrompt: (
    promptId: number,
    inputText: string,
    additionalInstructions?: string,
  ) =>
    request<{ output: string; prompt_id: number; version: number }>(`/prompts/preview`, {
      method: "POST",
      body: JSON.stringify({
        prompt_id: promptId,
        input_text: inputText,
        additional_instructions: additionalInstructions,
      }),
    }),

  getRfqAiSettings: (rfqId: number) =>
    request<RfqAiSetting>(`/rfq/${rfqId}/ai-settings`),

  saveRfqAiSettings: (
    rfqId: number,
    payload: { prompt_template_id: number | null; additional_instructions: string },
  ) =>
    request<RfqAiSetting>(`/rfq/${rfqId}/ai-settings`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
};
