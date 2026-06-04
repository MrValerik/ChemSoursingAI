// Тонкий HTTP-клиент к бэкенду. В dev запросы идут через Vite-прокси (/api -> :8000).

import type {
  RFQListItem,
  RFQPreview,
  RFQRead,
  SubstanceInfo,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
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
};
