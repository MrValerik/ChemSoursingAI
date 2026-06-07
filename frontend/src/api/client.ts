// Тонкий HTTP-клиент к бэкенду. В dev запросы идут через Vite-прокси (/api -> :8000).
// JWT-токен хранится в localStorage и добавляется в Authorization.

import type {
  EscalationRead,
  ExtractedQuote,
  QuotationRead,
  RFQListItem,
  RFQPreview,
  RFQRead,
  SubstanceInfo,
  SummaryRow,
  TokenResponse,
  UserRead,
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
};
