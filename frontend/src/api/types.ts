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
  owner_id: number | null;
  owner_name: string | null;
  rfq_subject: string | null;
  rfq_body: string | null;
}

export interface RFQListItem {
  owner_id: number | null;
  owner_name: string | null;
  n_quotations: number;
  n_complete: number;
  completeness_pct: number;
  has_open_escalation: boolean;
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
  rfq_name: string | null;
  rfq_cas: string | null;
  rfq_owner_name: string | null;
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

export interface PriceHistoryItem {
  rfq_id: number;
  date: string;
  price: number;
  currency: string | null;
  incoterm: string | null;
  moq: string | null;
}

export type SupplierTypeKind = "manufacturer" | "distributor";
export type ChannelKind = "email" | "whatsapp";
export type DispatchStatusKind = "queued" | "sent" | "delivered" | "read" | "error";

export interface SupplierRead {
  id: number;
  company: string;
  country: string | null;
  type: SupplierTypeKind | null;
  reputation: string | null;
  source: string | null;
  certificates: string[] | null;
  channels: ChannelKind[];
}

export interface RecipientRead {
  id: number;
  rfq_id: number;
  supplier_id: number;
  channel: ChannelKind;
  status: DispatchStatusKind;
  note: string | null;
  updated_at: string;
  supplier_company: string | null;
}

export type TemplateKind = "reply" | "followup" | "whatsapp";
export type WhatsappModeration = "draft" | "pending" | "approved" | "rejected";

export interface TemplateRead {
  id: number;
  kind: TemplateKind;
  name: string;
  body: string;
  version: number;
  moderation: WhatsappModeration | null;
  updated_by: string | null;
  updated_at: string;
}

export interface UserAdminRead extends UserRead {
  is_active: boolean;
}

export interface ChannelStatus {
  channel: string;
  title: string;
  configured: boolean;
  status: string;
  details: Record<string, string | null> | null;
}

export type PromptKind =
  | "extraction"
  | "rfq_generation"
  | "supplier_search"
  | "qualification"
  | "followup";

export interface PromptRead {
  id: number;
  kind: PromptKind;
  name: string;
  description: string | null;
  system_prompt: string;
  version: number;
  is_active: boolean;
  updated_by: string | null;
  updated_at: string;
}

export interface PromptVersionRead {
  id: number;
  prompt_id: number;
  version: number;
  name: string;
  description: string | null;
  system_prompt: string;
  changed_by: string | null;
  created_at: string;
}

export interface RfqAiSetting {
  rfq_id: number;
  prompt_template_id: number | null;
  additional_instructions: string;
}

export interface SupplierSearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface SupplierSearchResponse {
  query: string;
  ai_query: string | null;
  ai_used: boolean;
  fallback_used: boolean;
  results: SupplierSearchResult[];
  warning: string;
}

export type EvidenceStatus = "claimed" | "not_found" | "contradicted";
export type QualifiedSupplierType = "manufacturer" | "distributor" | "unknown";
export type CasEvidenceStatus = "confirmed" | "mentioned" | "not_found" | "mismatch";

export interface QualifiedSupplierResult extends SupplierSearchResult {
  result_index: number;
  company_name: string;
  title_ru: string;
  summary_ru: string;
  supplier_type: QualifiedSupplierType;
  cas_status: CasEvidenceStatus;
  gmp_status: EvidenceStatus;
  iso_status: EvidenceStatus;
  coa_status: EvidenceStatus;
  tds_status: EvidenceStatus;
  confidence: number;
  red_flags: string[];
  missing_evidence: string[];
}

export interface SupplierQualificationResponse {
  results: QualifiedSupplierResult[];
  prompt_id: number | null;
  prompt_version: number | null;
  warning: string;
}

export interface DashboardOverdue {
  id: number;
  name: string;
  cas: string;
  status: RFQStatus;
  owner_name: string | null;
  age_days: number;
}

export interface DashboardData {
  role: UserRole;
  in_work: number;
  attention: number;
  manual_review: number;
  by_status: Record<string, number>;
  overdue: DashboardOverdue[];
  workload?: { owner: string; count: number }[];
}
