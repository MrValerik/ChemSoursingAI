// Типы ответов бэкенда (соответствуют Pydantic-схемам).

export type RFQStatus =
  | "draft"
  | "verified"
  | "sent"
  | "collecting"
  | "parsed"
  | "summarized"
  | "escalated"
  | "closed";

export interface SubstanceInfo {
  cas: string;
  found: boolean;
  cid: number | null;
  iupac_name: string | null;
  molecular_formula: string | null;
  molecular_weight: number | null;
  synonyms: string[];
  source: string;
  error: string | null;
}

export interface RFQPreview {
  subject: string;
  body: string;
  fields: Record<string, unknown>;
}

export interface RFQRead {
  id: number;
  cas: string;
  name: string;
  purity: string | null;
  application: string | null;
  volume: string | null;
  target_price: number | null;
  currency: string | null;
  incoterms: string[] | null;
  channels: string[] | null;
  status: RFQStatus;
  verified: boolean;
  verification: SubstanceInfo | null;
  created_at: string;
  updated_at: string;
  rfq_subject: string | null;
  rfq_body: string | null;
}

export interface RFQListItem {
  id: number;
  cas: string;
  name: string;
  status: RFQStatus;
  verified: boolean;
  created_at: string;
}

export interface ExtractedQuote {
  price: number | null;
  currency: string | null;
  incoterm: string | null;
  moq: string | null;
  grade: string | null;
  payment_terms: string | null;
  lead_time: string | null;
  has_coa: boolean;
  has_tds: boolean;
  field_confidence: Record<string, number>;
  method: string;
}

export interface QuotationRead {
  id: number;
  rfq_id: number;
  manager_id: number | null;
  price: number | null;
  currency: string | null;
  incoterm: string | null;
  moq: string | null;
  grade: string | null;
  payment_terms: string | null;
  lead_time: string | null;
  has_coa: boolean;
  has_tds: boolean;
  is_complete: boolean;
  field_confidence: Record<string, number> | null;
  created_at: string;
  updated_at: string;
}

export interface SummaryRow {
  quotation_id: number;
  supplier: string | null;
  manager: string | null;
  price: number | null;
  currency: string | null;
  incoterm: string | null;
  moq: string | null;
  grade: string | null;
  lead_time: string | null;
  has_coa: boolean;
  has_tds: boolean;
  is_complete: boolean;
}

export interface EscalationRead {
  id: number;
  rfq_id: number;
  reason: string;
  status: string;
  assignee: string | null;
  note: string | null;
  created_at: string;
}

// --- Аутентификация (шаг 1 UI/UX-плана) ---

export type UserRole = "buyer" | "head" | "admin" | "auditor";

export interface UserRead {
  id: number;
  username: string;
  full_name: string;
  role: UserRole;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserRead;
}
