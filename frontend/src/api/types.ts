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

export interface CommunicationRead {
  id: number;
  rfq_id: number | null;
  manager_id: number | null;
  direction: "inbound" | "outbound";
  channel: ChannelKind;
  subject: string | null;
  body: string | null;
  from_address: string | null;
  to_address: string | null;
  status: "draft" | "sent" | "received" | "error" | null;
  thread_id: string | null;
  external_id: string | null;
  attachments: { filename: string; content_type: string; size: number }[] | null;
  created_at: string;
}

export interface EmailSyncRead {
  fetched: number;
  processed: number;
  duplicates: number;
  unmatched: number;
  quotations_created: number;
  followups_drafted: number;
  followups_sent: number;
  errors: string[];
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
  | "substance_identity"
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
  country_hint: "likely" | "possible" | "unknown";
}

export interface SubstanceIdentity {
  status: "verified" | "unverified" | "conflict" | "invalid_cas";
  canonical_name: string | null;
  search_names: string[];
  input_name_matches: boolean | null;
  substance_type: "single_substance" | "mixture" | "trade_name" | "unknown";
  ambiguities: string[];
}

export interface SearchPlanItem {
  query: string;
  language: "en" | "zh" | "ru" | "other";
  purpose: "manufacturer" | "product" | "documents" | "registry";
  source_type: "official_site" | "catalog" | "registry" | "web";
  priority: number;
}

export interface SupplierSearchResponse {
  search_run_id: number;
  query: string;
  queries_used: string[];
  identity: SubstanceIdentity;
  substance_lookup: {
    found: boolean;
    cid: number | null;
    iupac_name: string | null;
    molecular_formula: string | null;
    molecular_weight: number | null;
    source: string;
    error: string | null;
  };
  search_plan: SearchPlanItem[];
  ai_query: string | null;
  ai_used: boolean;
  fallback_used: boolean;
  results: SupplierSearchResult[];
  warning: string;
}

export type EvidenceStatus = "claimed" | "not_found" | "contradicted";
export type QualifiedSupplierType = "manufacturer" | "distributor" | "unknown";
export type CasEvidenceStatus = "confirmed" | "mentioned" | "not_found" | "mismatch";
export type CountryEvidenceStatus = "claimed" | "likely" | "not_found" | "mismatch";

export interface QualifiedSupplierResult extends SupplierSearchResult {
  result_index: number;
  company_name: string;
  title_ru: string;
  summary_ru: string;
  supplier_type: QualifiedSupplierType;
  cas_status: CasEvidenceStatus;
  country_status: CountryEvidenceStatus;
  gmp_status: EvidenceStatus;
  iso_status: EvidenceStatus;
  coa_status: EvidenceStatus;
  tds_status: EvidenceStatus;
  confidence: number;
  llm_confidence: number | null;
  score_breakdown: {
    total: number;
    identity: number;
    supplier_role: number;
    country: number;
    documents: number;
    evidence_quality: number;
    hard_exclusion: boolean;
    shortlist_eligible: boolean;
  };
  shortlist_eligible: boolean;
  red_flags: string[];
  missing_evidence: string[];
  evidence: QualifiedEvidence[];
}

export interface QualifiedEvidence {
  id: number;
  source_document_id: number;
  claim_type: string;
  claim_value: string;
  support_status: "supports" | "contradicts";
  quote: string;
  quote_verified: boolean;
}

export interface SupplierQualificationResponse {
  search_run_id: number;
  results: QualifiedSupplierResult[];
  prompt_id: number | null;
  prompt_version: number | null;
  warning: string;
}

export interface AgentRunRead {
  id: number;
  sequence: number;
  agent_slug: string;
  agent_name: string;
  execution_type: "llm" | "tool" | "deterministic" | string;
  status: string;
  prompt_id: number | null;
  prompt_version: number | null;
  effective_system_prompt: string | null;
  input_payload: Record<string, unknown> | null;
  output_payload: Record<string, unknown> | null;
  model: string | null;
  temperature: number | null;
  max_tokens: number | null;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
  error: string | null;
}

export interface SearchAttemptRead {
  id: number;
  agent_run_id: number | null;
  connector: string;
  query: string;
  language: string | null;
  source_type: string | null;
  purpose: string | null;
  status: string;
  result_count: number | null;
  results_payload: SupplierSearchResult[] | null;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
  error: string | null;
}

export interface SourceDocumentRead {
  id: number;
  agent_run_id: number | null;
  url: string;
  final_url: string | null;
  domain: string | null;
  title: string | null;
  content_type: string | null;
  status: string;
  http_status: number | null;
  text_content: string | null;
  content_hash: string | null;
  retrieved_at: string;
  error: string | null;
}

export interface EvidenceClaimRead {
  id: number;
  agent_run_id: number;
  source_document_id: number;
  result_index: number;
  claim_type: string;
  claim_value: string;
  support_status: "supports" | "contradicts";
  quote: string;
  quote_verified: boolean;
  created_at: string;
}

export interface SearchRunTrace {
  id: number;
  owner_id: number;
  owner_name: string | null;
  status: string;
  mode: string;
  input_payload: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  agent_runs: AgentRunRead[];
  search_attempts: SearchAttemptRead[];
  source_documents: SourceDocumentRead[];
  evidence_claims: EvidenceClaimRead[];
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
