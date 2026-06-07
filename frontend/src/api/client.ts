// Тонкий HTTP-клиент к бэкенду. В dev запросы идут через Vite-прокси (/api -> :8000).
// JWT-токен хранится в localStorage и добавляется в Authorization.

import type {
  EscalationRead,
  PriceHistoryItem,
  RecipientRead,
  SupplierRead,
  TemplateRead,
  UserRead,
  ExtractedQuote,
  QuotationRead,
  RFQListItem,
  RFQPreview,
  RFQRead,
  SubstanceInfo,
  SummaryRow,
  TokenResponse,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const TOKEN_KEY = "chemsource_token";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
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
    try {
      const data = await resp.json();
      detail = (data as { detail?: string }).detail ?? detail;
    } catch {
      /* тело не JSON — оставляем statusText */
    }
    throw new ApiError(resp.status, detail);
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

  previewRfq: (payload: RFQCreatePayload) =>
    request<RFQPreview>(`/rfq/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  createRfq: (payload: RFQCreatePayload, verify = true) =>
    request<RFQRead>(`/rfq?verify=${verify}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getRfq: (id: number) => request<RFQRead>(`/rfq/${id}`),

  listRfqs: () => request<RFQListItem[]>(`/rfq`),

  extractQuote: (text: string, useLlm = false) =>
    request<ExtractedQuote>(`/extraction/quote`, {
      method: "POST",
      body: JSON.stringify({ text, use_llm: useLlm }),
    }),

  extractAndStore: (rfqId: number, text: string, useLlm = false) =>
    request<QuotationRead>(`/rfq/${rfqId}/extract`, {
      method: "POST",
      body: JSON.stringify({ text, use_llm: useLlm }),
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

  addSupplier: (payload: {
    company: string;
    type?: string | null;
    country?: string | null;
    email?: string | null;
    whatsapp?: string | null;
  }) =>
    request<SupplierRead>(`/suppliers`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listRecipients: (rfqId: number) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/recipients`),

  selectRecipients: (rfqId: number, items: { supplier_id: number; channel: string }[]) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/recipients`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),

  dispatchRfq: (rfqId: number) =>
    request<RecipientRead[]>(`/rfq/${rfqId}/dispatch`, { method: "POST" }),

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

  listUsers: () => request<UserRead[]>(`/users`),

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
};
