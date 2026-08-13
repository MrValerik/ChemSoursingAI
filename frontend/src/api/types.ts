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
  /** Почему проверка не удалась — см. VerificationOutcome. */
  outcome: VerificationOutcome;
  cid: number | null;
  iupac_name: string | null;
  molecular_formula: string | null;
  molecular_weight: number | null;
  synonyms: string[];
  source: string;
  error: string | null;
}

export type SubstanceReviewStatus =
  | "unreviewed"
  | "needs_review"
  | "confirmed";

export interface SubstanceRecord {
  id: number;
  cas: string;
  preferred_name: string;
  synonyms: string[];
  excluded_names: string[];
  notes: string | null;
  review_status: SubstanceReviewStatus | string;
  verification: Record<string, unknown> | null;
  reviewed_by_id: number | null;
  reviewed_by_name: string | null;
  request_count: number;
  created_at: string;
  updated_at: string;
}

export interface SubstanceHistoryEntry {
  id: number;
  substance_id: number;
  action: string;
  changes: Record<
    string,
    {
      before: unknown;
      after: unknown;
    }
  >;
  snapshot: Record<string, unknown>;
  actor_id: number;
  actor_name: string | null;
  source_rfq_id: number | null;
  created_at: string;
}

export interface RFQPreview {
  subject: string;
  body: string;
  fields: Record<string, unknown>;
}

/**
 * Способ идентификации предмета закупки. CAS-номер есть не у всего, что
 * закупают: у смесей, рецептур и промышленных продуктов его нет и не
 * будет, но отправить по ним запрос поставщику вполне можно.
 */
export type IdentificationMethod = "cas" | "analog" | "spec";

/** Чем аналог может отличаться от эталона. */
export type AnalogVariation = "salt" | "purity" | "form" | "manufacturer";

/**
 * Источник значения поля. Находка ИИ-агента без пометки через месяц
 * неотличима от справочных данных.
 */
export type FieldSource = "pubchem" | "ai_agent" | "human" | "catalog";

/**
 * Чем закончилась проверка номера. Опечатка, отсутствие вещества в
 * PubChem и недоступность самого PubChem — три разных факта: последний
 * вообще не про вещество, а про доступность сервиса.
 */
export type VerificationOutcome =
  | "confirmed"
  | "not_found"
  | "invalid_checksum"
  | "unavailable"
  | "not_applicable";

export interface RFQRead {
  id: number;
  identification_method: IdentificationMethod;
  cas: string | null;
  name: string;
  analog_reference: string | null;
  analog_variations: string[] | null;
  specification: string | null;
  confirmed_synonyms: string[] | null;
  excluded_names: string[] | null;
  field_sources: Record<string, FieldSource> | null;
  purity: string | null;
  application: string | null;
  volume: string | null;
  target_price: number | null;
  currency: string | null;
  incoterms: string[] | null;
  channels: string[] | null;
  search_countries: string[] | null;
  supplier_target: number;
  status: RFQStatus;
  verified: boolean;
  verification: SubstanceInfo | null;
  substance_id: number | null;
  substance_preferred_name: string | null;
  substance_review_status: string | null;
  created_at: string;
  updated_at: string;
  owner_id: number | null;
  owner_name: string | null;
  rfq_subject: string | null;
  rfq_body: string | null;
  rfq_is_customized: boolean;
}

export interface RFQListItem {
  identification_method: IdentificationMethod;
  owner_id: number | null;
  owner_name: string | null;
  n_quotations: number;
  n_complete: number;
  completeness_pct: number;
  has_open_escalation: boolean;
  id: number;
  cas: string | null;
  name: string;
  status: RFQStatus;
  verified: boolean;
  search_countries: string[] | null;
  supplier_target: number;
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
export type SupplierQualificationStatus =
  | "candidate"
  | "under_review"
  | "verified"
  | "rejected";
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
  qualification_status: SupplierQualificationStatus;
  evidence_score: number | null;
  last_checked_at: string | null;
  contacts_count: number;
  request_count: number;
  contacts: {
    id: number;
    full_name: string | null;
    email: string | null;
    whatsapp: string | null;
    offered_substances: string[] | null;
  }[];
  /**
   * Почему связи нет: адрес скрыт подменой, на сайте только форма или
   * компанию назвала площадка и своей страницы у нас нет.
   */
  contact_barrier: "obfuscated" | "form" | "platform" | null;
  linked_requests: {
    rfq_id: number;
    name: string;
    // Номера может не быть: половина списка заказчика — марки и смеси.
    cas: string | null;
  }[];
  has_coa: boolean;
  has_tds: boolean;
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

export interface CommunicationMessageRead {
  id: number;
  direction: "inbound" | "outbound";
  channel: ChannelKind;
  subject: string | null;
  body: string | null;
  status: string | null;
  from_address: string | null;
  to_address: string | null;
  attachments: CommunicationAttachmentRead[] | null;
  created_at: string;
}

export interface CommunicationAttachmentRead {
  filename: string;
  content_type: string | null;
  size: number;
  document_id: number | null;
  kind: string | null;
  status: string;
  page_count: number | null;
  error: string | null;
}

export interface CommunicationEscalationRead {
  id: number;
  reason: string;
  status: string;
  assignee: string | null;
  note: string | null;
  communication_id: number | null;
  message_body: string | null;
  created_at: string;
}

export interface SupplierConversationRead {
  supplier_id: number | null;
  supplier_company: string;
  manager_id: number | null;
  manager_name: string | null;
  channel: ChannelKind;
  contact: string | null;
  recipient_status: DispatchStatusKind | null;
  data_collection_status: "not_started" | "collecting" | "complete" | "needs_human";
  missing_quote_fields: string[];
  last_message_at: string | null;
  messages: CommunicationMessageRead[];
  escalations: CommunicationEscalationRead[];
}

export interface CommunicationOverviewRead {
  conversations: SupplierConversationRead[];
  unassigned_escalations: CommunicationEscalationRead[];
}

export interface CommunicationTranslationRead {
  translations: Array<{
    message_id: number;
    translation_ru: string;
  }>;
}

export interface EmailSyncRead {
  fetched: number;
  processed: number;
  duplicates: number;
  unmatched: number;
  quotations_created: number;
  followups_drafted: number;
  followups_sent: number;
  escalations_created: number;
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

export interface EmailIntegration {
  channel: "email";
  enabled: boolean;
  configured: boolean;
  source: "database" | "environment";
  delivery_mode: "demo" | "live";
  email_from: string;
  email_from_name: string;
  email_timeout_s: number;
  auto_followup_mode: "off" | "draft" | "send";
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password_set: boolean;
  smtp_use_ssl: boolean;
  smtp_starttls: boolean;
  imap_host: string;
  imap_port: number;
  imap_user: string;
  imap_password_set: boolean;
  imap_use_ssl: boolean;
  imap_folder: string;
}

export interface WhatsAppIntegration {
  channel: "whatsapp";
  enabled: boolean;
  configured: boolean;
  source: "database" | "environment";
  transport: "cloud_api" | "web";
  web_gateway_available: boolean;
  phone_id: string;
  token_set: boolean;
  api_base_url: string;
  api_version: string;
  timeout_s: number;
}

export interface IntegrationConnectionResult {
  channel: "email" | "whatsapp";
  ok: boolean;
  message: string;
  details: Record<string, string | boolean | number | null>;
}

export interface WhatsAppWebStatus {
  state: string;
  ready: boolean;
  qr_available: boolean;
  pairing_code_available: boolean;
  pairing_code_expires_in_seconds: number;
  client_state: string | null;
  loading_percent: number | null;
  proxy_configured: boolean;
  account: string | null;
  pending_events: number;
  error: string | null;
}

export interface WhatsAppWebQr {
  qr_data_url: string;
}

export interface WhatsAppWebPairingCode {
  pairing_code: string;
  expires_in_seconds: number;
}

export interface CommunicationTestRun {
  id: number;
  rfq_id: number | null;
  quotation_id: number | null;
  channel: "email" | "whatsapp";
  recipient_masked: string;
  procurement_context: string;
  subject: string;
  customer_message: string;
  additional_instructions: string | null;
  generated_reply: string | null;
  model: string | null;
  reply_language: "en";
  simulation_mode: "buyer_ai" | "supplier_ai";
  delivery_mode: "preview" | "send";
  status:
    | "classifying"
    | "generating"
    | "previewed"
    | "sending"
    | "sent"
    | "complete"
    | "escalated"
    | "llm_error"
    | "delivery_error"
    | "processing_error";
  provider_message_id: string | null;
  error: string | null;
  created_at: string;
  messages: CommunicationTestMessage[];
  quote_assessment: CommunicationTestAssessment | null;
}

export interface CommunicationTestMessage {
  id: number;
  sender_role: "assistant" | "supplier" | "buyer";
  content: string;
  translation_ru: string | null;
  delivery_status: string;
  provider_message_id: string | null;
  created_at: string;
}

export interface CommunicationTestAssessment {
  is_complete: boolean;
  missing_fields: string[];
  low_confidence_fields: string[];
  price: number | null;
  currency: string | null;
  incoterm: string | null;
  moq: string | null;
  has_coa: boolean;
  has_tds: boolean;
}

export type PromptKind =
  | "extraction"
  | "rfq_generation"
  | "substance_identity"
  | "supplier_search"
  | "qualification"
  | "followup"
  | "supplier_communication";

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
  source_kind: "echemi" | "india_registry" | "india_web" | "web";
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
  // echemi_first встречается только в прогонах, сохранённых до перехода
  // на поиск по сайтам самих компаний.
  search_strategy: "direct_sites_first" | "echemi_first";
  source_counts: Record<string, number>;
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
  reserve_results?: SupplierSearchResult[];
  stop_reason?: string | null;
  budget?: SearchBudgetSnapshot;
  warning: string;
}

export interface SearchBudgetSnapshot {
  max_queries: number;
  queries_used: number;
  max_page_fetches: number;
  page_fetches_used: number;
  max_llm_calls: number;
  llm_calls_used: number;
  max_runtime_s: number;
  elapsed_s: number;
  stop_reason: string | null;
}

export type EvidenceStatus = "claimed" | "not_found" | "contradicted";
export type QualifiedSupplierType = "manufacturer" | "distributor" | "unknown";
export type CasEvidenceStatus = "confirmed" | "mentioned" | "not_found" | "mismatch";
export type CountryEvidenceStatus = "claimed" | "likely" | "not_found" | "mismatch";
export type SupplierVerificationStatus =
  | "confirmed"
  | "needs_review"
  | "rejected"
  | "unavailable";

export interface SupplierVerificationResult {
  status: SupplierVerificationStatus;
  model_status: "confirmed" | "needs_review" | "rejected" | null;
  substance_match: "exact" | "probable" | "analogue" | "mismatch" | "unknown";
  supplier_role: "manufacturer" | "distributor" | "unverified";
  recommended_action: "shortlist" | "manual_review" | "reject";
  confidence: number;
  reason: string;
  gate_reason: string;
  supporting_claim_ids: number[];
  contradictory_claim_ids: number[];
  invalid_claim_ids: number[];
  missing_evidence: string[];
}

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
  verification?: SupplierVerificationResult;
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
  verification_prompt_id?: number | null;
  verification_prompt_version?: number | null;
  registry_links?: Array<{ result_index: number; supplier_id: number }>;
  requested_supplier_count?: number;
  verified_source_count?: number;
  replacement_candidates_used?: number;
  source_shortfall?: number;
  budget?: SearchBudgetSnapshot;
  warning: string;
}

export interface AgentRunRead {
  id: number;
  sequence: number;
  agent_slug: string;
  agent_name: string;
  execution_type: "llm" | "tool" | "deterministic" | string;
  contract_version: string;
  status: string;
  prompt_id: number | null;
  prompt_version: number | null;
  effective_system_prompt: string | null;
  input_payload: Record<string, unknown> | null;
  output_payload: Record<string, unknown> | null;
  raw_output_payload: Record<string, unknown> | null;
  parsed_output_payload: Record<string, unknown> | null;
  validation_output_payload: Record<string, unknown> | null;
  policy_output_payload: Record<string, unknown> | null;
  events: AgentEvent[] | null;
  model: string | null;
  temperature: number | null;
  max_tokens: number | null;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
  /** Расход токенов этапа: вход дороже выхода вчетверо по объёму. */
  prompt_tokens: number | null;
  completion_tokens: number | null;
  error: string | null;
}

export interface AgentEvent {
  at: string;
  kind: "action" | "info" | "warning" | "error" | string;
  message: string;
  data?: Record<string, unknown>;
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

export interface SearchRunListItem {
  id: number;
  correlation_id: string;
  graph_version: string;
  owner_id: number;
  rfq_id: number | null;
  parent_search_run_id: number | null;
  replay_mode: string | null;
  owner_name: string | null;
  status: string;
  mode: string;
  input_payload: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
  error: string | null;
  queue_position: number | null;
  result_count: number;
  is_stale: boolean;
  can_restart: boolean;
  can_resume: boolean;
  summary: {
    planned_query_count: number;
    executed_query_count: number;
    raw_page_count: number;
    candidate_count: number;
    qualified_count: number;
    manufacturer_candidate_count: number;
    qualification_status: string;
  };
}

export interface SearchRunTrace extends SearchRunListItem {
  result_payload: SupplierSearchResponse | null;
  agent_runs: AgentRunRead[];
  search_attempts: SearchAttemptRead[];
  source_documents: SourceDocumentRead[];
  evidence_claims: EvidenceClaimRead[];
  candidate_results: SupplierSearchResult[];
  qualified_results: QualifiedSupplierResult[];
  merged_run_count: number;
}

export interface SupplierSearchJob {
  search_run_id: number;
  correlation_id: string;
  status: "queued";
  queue_position: number;
}

export interface SearchRunReplay {
  search_run_id: number;
  correlation_id: string;
  parent_search_run_id: number;
  replay_mode: "validator";
  status: string;
}

export interface DocumentClaimRead {
  claim_type: string;
  claim_value: string;
  quote: string;
  quote_verified?: boolean;
  rejection_reason?: string;
}

export interface DocumentVerificationResult {
  status: "confirmed" | "needs_review" | "rejected" | "unavailable";
  model_status: string | null;
  document_kind: string | null;
  substance_match: string;
  recommended_action: string;
  confidence: number;
  reason: string;
  gate_reason: string;
  accepted_claims: DocumentClaimRead[];
  rejected_claims: DocumentClaimRead[];
  missing_fields: string[];
  red_flags: string[];
  cas_in_document: string[];
  expected_cas: string | null;
  expected_name?: string | null;
}

export interface SupplierDocumentRead {
  id: number;
  rfq_id: number | null;
  communication_id: number | null;
  supplier_id: number | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  kind: string;
  text_status:
    | "stored"
    | "extracted"
    | "ocr_extracted"
    | "needs_ocr"
    | "unsupported"
    | "failed";
  page_count: number | null;
  extraction_error: string | null;
  extracted_at: string | null;
  verification: DocumentVerificationResult | null;
  created_at: string;
}

export interface SupplierDocumentDetail extends SupplierDocumentRead {
  text_content: string | null;
}

// Реестр посредников: площадки, каталоги и перекупщики, которые не являются
// сайтами производителей. Используется для отсева выдачи до загрузки страниц.
export type IntermediaryKind = "marketplace" | "catalog" | "reseller" | "reference";

export interface IntermediaryRead {
  id: number;
  domain: string;
  name: string;
  kind: IntermediaryKind;
  notes: string | null;
  is_active: boolean;
}

// Режим поиска: только изготовители или все продавцы (для сравнения цен).
export type SearchScope = "manufacturers" | "all_sellers";
