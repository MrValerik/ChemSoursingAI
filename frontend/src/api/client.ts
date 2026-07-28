// Тонкий HTTP-клиент к бэкенду. В dev запросы идут через Vite-прокси (/api -> :8000).
// JWT-токен хранится в localStorage и добавляется в Authorization.

import type {
  ChannelStatus,
  CommunicationRead,
  DashboardData,
  EmailSyncRead,
  EscalationRead,
  PriceHistoryItem,
  PromptRead,
  PromptVersionRead,
  RecipientRead,
  SearchRunListItem,
  SupplierRead,
  SupplierQualificationResponse,
  SupplierSearchJob,
  SupplierSearchResponse,
  SearchRunTrace,
  TemplateRead,
  UserAdminRead,
  UserRead,
  ExtractedQuote,
  QuotationRead,
  RFQListItem,
  RFQPreview,
  RFQRead,
  RfqAiSetting,
  SubstanceInfo,
  SubstanceRecord,
  SummaryRow,
  TokenResponse,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const TOKEN_KEY = "chemsource_token";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public searchRunId: number | null = null,
  ) {
    super(message);
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

  const resp = await fetch(`${BASE}${path}`, { headers, ...options });
  if (!resp.ok) {
    if (resp.status === 401 && !path.startsWith("/auth/login")) {
      onUnauthorized?.();
    }
    let detail = resp.statusText;
    let searchRunId: number | null = null;
    try {
      const data = await resp.json();
      const errorDetail = (data as {
        detail?: string | { message?: string; search_run_id?: number };
      }).detail;
      if (typeof errorDetail === "string") {
        detail = errorDetail;
      } else if (errorDetail) {
        detail = errorDetail.message ?? detail;
        searchRunId = errorDetail.search_run_id ?? null;
      }
    } catch {
      /* тело не JSON — оставляем statusText */
    }
    throw new ApiError(resp.status, detail, searchRunId);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export interface RFQCreatePayload {
  cas: string;
  name: string;
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

  listRecipients: (rfqId: number) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/recipients`),

  selectRecipients: (rfqId: number, items: { supplier_id: number; channel: string }[]) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/recipients`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),

  dispatchRfq: (rfqId: number) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/dispatch`, { method: "POST" }),

  listCommunications: (rfqId: number) =>
    request<CommunicationRead[]>(`/rfq/${rfqId}/communications`),

  syncEmail: (limit = 5) =>
    request<EmailSyncRead>(`/email/sync?limit=${limit}`, { method: "POST" }),

  sendCommunicationDraft: (communicationId: number) =>
    request<CommunicationRead>(`/communications/${communicationId}/send`, {
      method: "POST",
    }),

  removeRecipient: (rfqId: number, recipientId: number) =>
    request<void>(`/rfq/${rfqId}/recipients/${recipientId}`, { method: "DELETE" }),

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

  dashboard: () => request<DashboardData>(`/dashboard`),

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
    cas: string;
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
      cas: string;
      name: string;
      country?: string | null;
      additional_instructions?: string | null;
      limit?: number;
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
    cas: string;
    name: string;
    country?: string | null;
    additional_instructions?: string | null;
    results: SupplierSearchResponse["results"];
  }) =>
    request<SupplierQualificationResponse>(`/supplier-search/qualify`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSearchRun: (id: number) =>
    request<SearchRunTrace>(`/search-runs/${id}`),

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
