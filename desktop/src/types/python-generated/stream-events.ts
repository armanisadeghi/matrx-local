// AUTO-GENERATED — do not edit manually.
// Sources:
//   matrx_connect.context.*  — stream event types
//   matrx_ai.db.message_parts — cx_message.content[] types
// Run: uv run python scripts/generate_types.py stream

export const EventType = {
  CHUNK: "chunk",
  REASONING_CHUNK: "reasoning_chunk",
  REASONING: "reasoning",
  PHASE: "phase",
  WARNING: "warning",
  INFO: "info",
  DATA: "data",
  INIT: "init",
  COMPLETION: "completion",
  ERROR: "error",
  TOOL_EVENT: "tool_event",
  HEARTBEAT: "heartbeat",
  END: "end",
  RENDER_BLOCK: "render_block",
  RECORD_RESERVED: "record_reserved",
  RECORD_UPDATE: "record_update",
  RESOURCE_CHANGED: "resource_changed",
  CONTEXT_ANALYSIS: "context_analysis",
  STRUCTURED_OUTPUT: "structured_output",
  CONTEXT_STATE: "context_state",
  CONTEXT_TRIMMED: "context_trimmed",
  INJECTION_CONSUMED: "injection_consumed",
  PROVIDER_RETRY: "provider_retry",
  CITATION: "citation",
  CONTROL_TOKEN: "control_token",
} as const;

export type EventType = (typeof EventType)[keyof typeof EventType];

export type Phase =
  | "connected"
  | "processing"
  | "generating"
  | "using_tools"
  | "persisting"
  | "searching"
  | "scraping"
  | "analyzing"
  | "synthesizing"
  | "retrying"
  | "executing"
  | "complete";

export type Operation =
  | "llm_request"
  | "tool_execution"
  | "user_request"
  | "sub_agent"
  | "persistence";

export type ToolEventType =
  | "tool_started"
  | "tool_progress"
  | "tool_step"
  | "tool_result_preview"
  | "tool_completed"
  | "tool_error"
  | "tool_delegated";

export type WarningLevel =
  | "low"
  | "medium"
  | "high";

export type InitCompletionStatus =
  | "success"
  | "failed"
  | "cancelled";

export interface ChunkPayload {
  text: string;
}

export interface CitationPayload {
  block_index?: number | null;
  citation: Record<string, unknown>;
}

export interface ControlTokenPayload {
  name: string;
  value: string;
  declared_by?: string | null;
}

export interface ReasoningChunkPayload {
  text: string;
}

export interface ReasoningPayload {
  state: "started" | "stopped";
}

export interface PhasePayload {
  phase: "analyzing" | "complete" | "connected" | "executing" | "generating" | "persisting" | "processing" | "retrying" | "scraping" | "searching" | "synthesizing" | "using_tools";
}

export interface WarningPayload {
  code: string;
  system_message: string;
  user_message?: string | null;
  level?: "high" | "low" | "medium";
  recoverable?: boolean;
  metadata?: Record<string, unknown>;
}

export interface InfoPayload {
  code: string;
  system_message: string;
  user_message?: string | null;
  metadata?: Record<string, unknown>;
}

export interface InitPayload {
  operation: "llm_request" | "persistence" | "sub_agent" | "tool_execution" | "user_request";
  operation_id: string;
  parent_operation_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CompletionPayload {
  operation: "llm_request" | "persistence" | "sub_agent" | "tool_execution" | "user_request";
  operation_id: string;
  status: "cancelled" | "failed" | "success";
  result?: Record<string, unknown>;
}

export interface ErrorPayload {
  error_type: string;
  message: string;
  user_message?: string;
  code?: string | null;
  details?: Record<string, unknown> | null;
}

export interface ProviderRetryPayload {
  state: "cancelled" | "recovered" | "retrying_now" | "scheduled" | "suspended";
  provider: string;
  error_type: string;
  message: string;
  user_message: string;
  status_code?: number | null;
  model?: string | null;
  request_id?: string | null;
  conversation_id?: string | null;
  iteration: number;
  failed_attempt: number;
  next_attempt?: number | null;
  max_retries: number;
  retry_delay?: number | null;
  retry_at?: number | null;
  discard_partial_output?: boolean;
  schedule?: number[];
  can_cancel?: boolean;
  can_retry_now?: boolean;
  actions?: Record<string, string>;
}

export interface ToolEventPayload {
  event: "tool_completed" | "tool_delegated" | "tool_error" | "tool_progress" | "tool_result_preview" | "tool_started" | "tool_step";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data?: Record<string, unknown>;
}

export interface HeartbeatPayload {
  timestamp?: number;
  seq?: number | null;
  late_by_seconds?: number | null;
}

export interface EndPayload {
  reason?: string;
}

export interface RenderBlockPayload {
  blockId: string;
  blockIndex: number;
  type: string;
  status: "complete" | "error" | "streaming";
  content?: string | null;
  data?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
}

export interface RecordReservedPayload {
  db_project: string;
  table: string;
  record_id: string;
  status?: "pending";
  parent_refs?: Record<string, string>;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
}

export interface RecordUpdatePayload {
  db_project: string;
  table: string;
  record_id: string;
  status: "active" | "completed" | "failed";
  metadata?: Record<string, unknown>;
  created_at?: string | null;
}

export interface ResourceChangedPayload {
  kind: string;
  action: "created" | "deleted" | "invalidated" | "modified" | "moved" | "renamed";
  resource_id: string;
  sandbox_id?: string | null;
  user_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ContextAnalysisPayload {
  provider: string;
  model?: string | null;
  iteration?: number | null;
  conversation_id?: string | null;
  request_id?: string | null;
  attempt?: number;
  is_streaming?: boolean;
  method: string;
  url: string;
  headers: Record<string, string>;
  body?: Record<string, unknown> | null;
  body_raw?: string | null;
  body_size_bytes?: number;
  timestamp?: number;
}

export interface StructuredOutputPayload {
  schema_name?: string | null;
  json_schema: Record<string, unknown>;
  data?: Record<string, unknown> | unknown[] | null;
  success: boolean;
  reason?: string;
  match_count?: number;
  agent_name?: string | null;
  operation_id?: string | null;
  kind?: string | null;
  kind_version?: number | null;
  kind_checked?: boolean;
  kind_errors?: string[];
}

export interface ConsumedInjection {
  injection_id: string;
  kind: string;
  text?: string | null;
  is_visible_to_user?: boolean;
  position?: number | null;
  message_id?: string | null;
}

export interface InjectionConsumedPayload {
  conversation_id: string;
  items: ConsumedInjection[];
  count: number;
}

export interface ContextStatePayload {
  conversation_id: string;
  last_request_input_tokens?: number;
  last_request_cached_tokens?: number;
  last_request_output_tokens?: number;
  total_chars_visible_to_model?: number;
  message_count_visible?: number;
  cache_state?: Record<string, unknown>;
  measured_at: string;
}

export interface ContextTrimmedPayload {
  conversation_id: string;
  request_id?: string | null;
  trim_summary: Record<string, unknown>;
  measured_at: string;
}

// --- Typed Record Reservation Variants (discriminated on `table`) ---

// Narrows RecordReservedPayload.metadata / parent_refs by table.
// Server guarantees these shapes for message, request, tool_call.

export interface CxMessageReservedParentRefs {
  [key: string]: unknown;
  conversation_id: string;
  user_request_id: string;
}

export interface CxMessageReservedMetadata {
  [key: string]: unknown;
  role: "assistant" | "system" | "tool" | "user";
  position: number;
}

export interface CxRequestReservedParentRefs {
  [key: string]: unknown;
  conversation_id: string;
  user_request_id: string;
}

export interface CxRequestReservedMetadata {
  [key: string]: unknown;
  iteration: number;
}

export interface CxToolCallReservedParentRefs {
  [key: string]: unknown;
  conversation_id: string;
  user_request_id: string;
  call_id: string;
}

export interface CxToolCallReservedMetadata {
  [key: string]: unknown;
  tool_name: string;
  call_id: string;
  iteration: number;
}

export type CxMessageReservedPayload = RecordReservedPayload & {
  table: "message";
  parent_refs: CxMessageReservedParentRefs;
  metadata: CxMessageReservedMetadata;
};

export type CxRequestReservedPayload = RecordReservedPayload & {
  table: "request";
  parent_refs: CxRequestReservedParentRefs;
  metadata: CxRequestReservedMetadata;
};

export type CxToolCallReservedPayload = RecordReservedPayload & {
  table: "tool_call";
  parent_refs: CxToolCallReservedParentRefs;
  metadata: CxToolCallReservedMetadata;
};

/** Discriminated union on `table` — narrows metadata/parent_refs for known tables. */
export type TypedRecordReservedPayload =
  | CxMessageReservedPayload
  | CxRequestReservedPayload
  | CxToolCallReservedPayload;

/** True when the reservation is for a known table with typed metadata. */
export function isTypedRecordReservedPayload(p: RecordReservedPayload): p is RecordReservedPayload & TypedRecordReservedPayload {
  return p.table === "message" || p.table === "request" || p.table === "tool_call";
}

/** Narrows to CxMessageReservedPayload — `metadata.role` and `metadata.position` are guaranteed. */
export function isCxMessageReservation(p: RecordReservedPayload): p is CxMessageReservedPayload {
  return p.table === "message";
}

/** Narrows to CxRequestReservedPayload — `metadata.iteration` is guaranteed. */
export function isCxRequestReservation(p: RecordReservedPayload): p is CxRequestReservedPayload {
  return p.table === "request";
}

/** Narrows to CxToolCallReservedPayload — `metadata.tool_name`, `metadata.call_id`, and `metadata.iteration` are guaranteed. */
export function isCxToolCallReservation(p: RecordReservedPayload): p is CxToolCallReservedPayload {
  return p.table === "tool_call";
}

// --- Typed Data Payloads ---

export interface DataPayload {
  type: string;
}

export interface AgentStudioFromChatProgressData {
  type?: "agent_studio_from_chat_progress";
  step: "briefing" | "building" | "proving" | "reading";
  says: string;
  build_id?: string | null;
  proof_cases?: number | null;
  run_conversation_id?: string | null;
  brief?: Record<string, unknown> | null;
  scope?: Record<string, unknown> | null;
}

export interface AgentStudioBriefVariable {
  name: string;
  description?: string;
  test_value?: string;
}

export interface AgentStudioFromChatResultData {
  type?: "agent_studio_from_chat_result";
  conversation_id: string;
  agent_id: string;
  version_id: string;
  agent_name: string;
  summary?: string;
  deliverable?: string;
  goals?: string[];
  variables?: AgentStudioBriefVariable[];
  fit?: string;
  fit_reason?: string;
  accepted_turns?: number[];
  accepted_result?: string | null;
  proof_output?: string | null;
  proof_error?: string | null;
  brief_conversation_id?: string | null;
  builder_conversation_id?: string | null;
  proof_conversation_id?: string | null;
  says: string;
}

export interface AgentTestProgressEvent {
  type?: "agent_test_progress";
  run_id: string;
  counts?: Record<string, unknown>;
}

export interface AgentTestResultEvent {
  type?: "agent_test_result";
  run_id: string;
  result_id: string;
  verdict?: string | null;
  door?: string | null;
  setup_key?: string | null;
}

export interface AgentTestRunStartedEvent {
  type?: "agent_test_run_started";
  run_id: string;
  execution_id: string;
  result_count?: number;
  status?: string;
}

export interface AgentTestRunStatusEvent {
  type?: "agent_test_run_status";
  run_id: string;
  status: string;
}

export interface AssignmentProgressData {
  type?: "assignment_progress";
  session_id: string;
  completed: number;
  total: number;
  status: "cancelled" | "completed" | "failed" | "partially_failed" | "pending" | "running";
}

export interface AudioOutputData {
  type?: "audio_output";
  url: string;
  mime_type: string;
  file_id?: string | null;
  cdn_url?: string | null;
  download_url?: string | null;
}

export interface AudioStreamChunkData {
  type?: "audio_stream_chunk";
  stream_id: string;
  take?: number;
  seq: number;
  audio_base64: string;
  mime_type?: string;
  encoding?: "mp3" | "pcm_s16le";
  sample_rate?: number;
  bits_per_sample?: number;
  channels?: number;
}

export interface AudioStreamEndData {
  type?: "audio_stream_end";
  stream_id: string;
  take?: number;
  total_chunks: number;
  url?: string;
  mime_type?: string;
  file_id?: string | null;
  cdn_url?: string | null;
  download_url?: string | null;
  duration_ms?: number | null;
  sample_rate?: number;
  bits_per_sample?: number;
  channels?: number;
}

export interface EsignBulkSendCreatedData {
  type?: "bulk_send_created";
  campaign_id: string;
  rows: number;
}

export interface EsignBulkSendResultData {
  type?: "bulk_send_result";
  campaign_id: string;
  total: number;
  sent?: number;
  failed?: number;
  reminded?: number;
  status?: string | null;
}

export interface EsignBulkSendRowData {
  type?: "bulk_send_row";
  done: number;
  total: number;
  member_id?: string | null;
  row_no?: number | null;
  full_name?: string | null;
  email?: string | null;
  status: string;
  envelope_id?: string | null;
  reason?: string | null;
}

export interface EsignBulkSendRowProblem {
  row_no: number;
  role_key?: string | null;
  code: string;
  message: string;
}

export interface EsignBulkSendRowsInvalidData {
  type?: "bulk_send_rows_invalid";
  problems: EsignBulkSendRowProblem[];
}

export interface CategorizationResultData {
  type?: "categorization_result";
  prompt_id: string;
  category: string;
  tags?: string[];
  description?: string;
  dry_run?: boolean;
  metadata?: Record<string, unknown>;
}

export interface JsonValue {
}

export interface ClaudeManagedSdkMessageData {
  type?: "claude_managed_sdk_message";
  runtime_id: string;
  session_id: string;
  sdk_message_type: string;
  message: Record<string, JsonValue>;
}

export interface ClaudeManagedWarningData {
  type?: "claude_managed_warning";
  code: string;
  runtime_id: string;
  session_id: string;
}

export interface CmsLogoFoundData {
  type?: "cms_logo_found";
  site_id: string;
  found: boolean;
  message: string;
  asset_id?: string | null;
  asset_url?: string | null;
  source?: string | null;
  source_url?: string | null;
  width?: number | null;
  height?: number | null;
  mime_type?: string | null;
  candidates_considered?: number;
  rejected?: string[];
}

export interface ContextChangedData {
  type?: "context_changed";
  key: string;
  command: string;
  object_type?: string;
  mutable?: boolean;
  persist?: string;
  source_kind?: string;
  source_id?: string | null;
}

export interface ContextConflictData {
  type?: "context_conflict";
  key: string;
  command: string;
  source_kind?: string;
  source_id?: string | null;
  base_version?: number | null;
}

export interface ContextDeltaData {
  type?: "context_delta";
  key: string;
  command: string;
  object_type?: string;
  source_kind?: string;
  source_id?: string | null;
  seq?: number;
  delta_kind?: "full" | "splice";
  start?: number | null;
  end?: number | null;
  text?: string | null;
  base_len?: number | null;
  new_len?: number;
  content?: string | null;
}

export interface ContextPersistFailedData {
  type?: "context_persist_failed";
  key: string;
  command: string;
  source_kind?: string;
  source_id?: string | null;
  error?: string;
  traceback?: string;
}

export interface ContextPersistedData {
  type?: "context_persisted";
  key: string;
  command: string;
  source_kind?: string;
  source_id?: string | null;
  materialized?: boolean;
}

export interface ContextDecidedBy {
  include: "agent" | "default" | "page" | "you";
  max_inline_chars: "agent" | "default" | "page" | "you";
}

export interface ContextDeliveredRef {
  chars: number;
  sha256: string;
}

export interface ContextReceiptBlock {
  id: string;
  label: string;
  delivered: ContextDeliveredRef;
}

export interface ContextReceiptPassthrough {
  conductor_id: string;
  mode: "closed" | "handed_over" | "mirror";
  person_and_org: boolean;
  handed_over?: string[];
  mirrored?: string[];
  declared?: string[];
  withheld?: ContextReceiptPassthroughWithheld[];
  overrides?: ContextReceiptPassthroughOverride[];
}

export interface ContextReceiptPassthroughOverride {
  key: string;
  show?: "pointer" | "whole" | null;
  limit?: number | null;
}

export interface ContextReceiptPassthroughWithheld {
  key: string;
  reason: "closed" | "conductor_owned" | "page_off" | "person_and_org_off" | "refused";
}

export interface ContextReceiptRoomView {
  participant_id: string;
  participant_key: string;
  round: number;
  policy_version: number;
  included?: string[];
  digested?: string[];
  withheld?: ContextReceiptWithheldItem[];
  error?: string | null;
}

export interface ContextReceiptRow {
  key: string;
  label: string;
  surface_key: string;
  origin: "client" | "rule" | "server" | "surroundings";
  chars?: number | null;
  include: boolean;
  max_inline_chars: number;
  delivery: "inline" | "off" | "on_request";
  decided_by: ContextDecidedBy;
  user_rule?: ContextRule | null;
  clamped?: boolean;
  client_sent_excluded?: boolean;
  blocked_by?: "model" | "self_check" | null;
  consumed_as?: "directive" | "expanded" | "renamed" | "unaccounted" | null;
  consumed_into?: string[];
  delivered?: ContextDeliveredRef | null;
  on_request?: ContextDeliveredRef | null;
}

export interface ContextReceiptWithheldItem {
  message_id: string;
  conversation_id: string;
  speaker: string;
  rule: string;
}

export interface ContextRule {
  include?: boolean | null;
  max_inline_chars?: number | null;
}

export interface ContextReceiptData {
  type?: "context_receipt";
  version?: 1;
  surface?: string | null;
  cap: number;
  model_reads_context?: boolean;
  rows?: ContextReceiptRow[];
  rules_error?: string | null;
  blocks?: ContextReceiptBlock[];
  room_view?: ContextReceiptRoomView | null;
  passthrough?: ContextReceiptPassthrough | null;
}

export interface ConversationIdData {
  type?: "conversation_id";
  conversation_id: string;
}

export interface ConversationLabeledData {
  type?: "conversation_labeled";
  conversation_id: string;
  title: string;
  description?: string;
  keywords?: string[];
}

export interface CutoverCopyAgainProgressData {
  type?: "cutover_copy_again_progress";
  organization_id: string;
  table_id?: string | null;
  table_name: string;
  done: number;
  total: number;
  refused?: string | null;
  says: string;
}

export interface CutoverCopyAgainReportData {
  type?: "cutover_copy_again_report";
  organization_id: string;
  organization_name: string;
  table_id?: string | null;
  table_name?: string | null;
  ok: boolean;
  tables: number;
  tables_refused?: string[];
  rows_written?: number;
  carried?: Record<string, number>;
  named?: string[];
  says: string;
}

export interface CutoverFinalSwitchCopyAgainResultData {
  type?: "cutover_final_switch_copy_again_result";
  run_id: string;
  resumed?: boolean;
  ok: boolean;
  says: string;
  organizations?: Record<string, unknown>[];
  still_needs?: string[];
  timings_ms?: Record<string, number>;
}

export interface CutoverFinalSwitchResultData {
  type?: "cutover_final_switch_result";
  direction: "new" | "old";
  ok: boolean;
  reason?: string | null;
  says: string;
  press_id?: string | null;
  counts?: Record<string, unknown>;
  timings_ms?: Record<string, number>;
  copied_again?: string[];
  not_carried?: string[];
}

export interface CutoverFinalSwitchStageData {
  type?: "cutover_final_switch_stage";
  stage: string;
  says: string;
}

export interface DecisionAnswerBlock {
  __kind?: "decision_answer";
  type: "box" | "choice" | "noul" | "score";
  answer: DecisionBoxBlock | boolean | number | string;
  probability?: number | null;
  probabilities?: Record<string, number> | null;
  confidence?: number | null;
  legend?: Record<string, string> | null;
  image?: number | null;
}

export interface DecisionBoxBlock {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface DecisionImageBlock {
  file_id?: string | null;
  url?: string | null;
  width?: number | null;
  height?: number | null;
}

export interface DecisionUsageBlock {
  input_tokens: number;
  output_tokens: number;
}

export interface DecisionAnswersData {
  type?: "decision_answers";
  __kind: "decision_answers";
  model: string;
  method: "native" | "verbalized" | "verbalized_calibrated";
  answers?: Record<string, DecisionAnswerBlock>;
  unanswerable?: Record<string, string>;
  usage: DecisionUsageBlock;
  cost_usd: number;
  images?: DecisionImageBlock[];
}

export interface DecisionReferenceItemData {
  type?: "decision_reference_item";
  run_id: string;
  item_id: string;
  question: string;
  reference_id?: string | null;
  reference_answer: Record<string, number> | boolean | number | string;
  reference_verdict: string;
  reference_p?: number | null;
  reference_probability?: number | null;
  reference_probabilities?: Record<string, number> | null;
  reference_legend?: Record<string, string> | null;
  item_verdict: string;
  item_p_answer?: number | null;
  item_probability?: number | null;
  item_probabilities?: Record<string, number> | null;
  item_confidence?: number | null;
  agree: boolean;
  already_checked?: boolean;
  model?: string | null;
  cost_usd: number;
  latency_ms: number;
}

export interface DecisionReferenceItemFailedData {
  type?: "decision_reference_item_failed";
  run_id: string;
  item_id: string;
  question: string;
  error: string;
  status?: "failed" | "in_flight" | "unanswerable" | "unbuildable";
  reference_id?: string | null;
  already_checked?: boolean;
}

export interface DecisionReferenceRunDoneData {
  type?: "decision_reference_run_done";
  run_id: string;
  done: number;
  failed: number;
  unanswerable?: number;
  in_flight?: number;
  agreement_rate?: number | null;
  cost_usd: number;
}

export interface DecisionReferenceModel {
  id: string;
  name: string;
}

export interface DecisionReferenceRunStartedData {
  type?: "decision_reference_run_started";
  run_id: string;
  agent_id: string;
  item_count: number;
  skipped_existing?: number;
  in_flight?: number;
  unbuildable?: number;
  reference_model: DecisionReferenceModel;
  judge_key: string;
}

export interface DictionaryPublishCompleteData {
  type?: "dictionary_publish_complete";
  status: string;
  level: string;
  owner_id?: string | null;
  external_id?: string | null;
  version_id?: string | null;
  rule_count?: number;
  rules_hash?: string | null;
}

export interface QuestionnaireQuestion {
  id: string;
  prompt: string;
  component_type: "checkboxes" | "dropdown" | "input" | "radio" | "slider" | "textarea" | "toggle";
  options?: string[];
  min?: number | null;
  max?: number | null;
  step?: number | null;
  default?: unknown;
  required?: boolean;
}

export interface QuestionnaireDisplayData {
  type?: "display_questionnaire";
  introduction: string;
  questions?: QuestionnaireQuestion[];
}

export interface ExtractionIndexCompleteData {
  type?: "extraction_index_complete";
  run_id: string;
  job_id: string;
  derivative_id: string;
  derivative_outcome: string;
  results_total: number;
  chunks_written: number;
  chunks_skipped: number;
  priority_applied: number;
  agent_id?: string | null;
  embedding_model: string;
}

export interface ExtractionIndexProgressData {
  type?: "extraction_index_progress";
  run_id: string;
  stage: string;
  message?: string;
  results_total?: number;
  chunks_built?: number;
  chunks_skipped?: number;
  embedded?: number;
  total_to_embed?: number;
}

export interface FetchResultItem {
  [key: string]: unknown;
  url?: string;
  title?: string;
  content?: string;
  status?: string;
}

export interface FetchResultsData {
  type?: "fetch_results";
  metadata?: Record<string, unknown>;
  results?: FetchResultItem[];
}

export interface FileAnalysisCompleteData {
  type?: "file_analysis_complete";
  file_id: string;
  status: string;
  results_count?: number;
  failures?: string[];
  elapsed_ms?: number;
  head?: Record<string, unknown>;
  results?: Record<string, unknown>[];
}

export interface FileAnalysisStartedData {
  type?: "file_analysis_started";
  file_id: string;
  mime_type?: string | null;
  total_detectors: number;
}

export interface FileDetectorCompletedData {
  type?: "file_detector_completed";
  file_id: string;
  detector_kind: string;
  tier: string;
  status: string;
  elapsed_ms?: number;
  error?: string | null;
  complete: number;
  total: number;
}

export interface FileSearchBboxItem {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface FileSearchHitItem {
  page_number: number;
  page_id?: string | null;
  bbox: FileSearchBboxItem;
  snippet: string;
  matched_text: string;
  char_start?: number | null;
  char_end?: number | null;
}

export interface FileSearchCompleteData {
  type?: "file_search_complete";
  file_id: string;
  query: string;
  regex?: boolean;
  case_sensitive?: boolean;
  hits?: FileSearchHitItem[];
  truncated?: boolean;
  pages_scanned?: number;
  total_pages?: number;
}

export interface FileSearchPageData {
  type?: "file_search_page";
  page_number: number;
  total_pages: number;
  hits?: FileSearchHitItem[];
  total_hits?: number;
}

export interface FileSearchStartedData {
  type?: "file_search_started";
  file_id: string;
  query: string;
  regex?: boolean;
  case_sensitive?: boolean;
  total_pages: number;
}

export interface FirstTurnFilledData {
  type?: "first_turn_filled";
  system_instruction?: string | null;
  messages?: Record<string, unknown>[];
  wire_swaps?: string | null;
}

export interface FunctionResultData {
  type?: "function_result";
  function_name: string;
  success: boolean;
  result?: unknown;
  error?: string | null;
  duration_ms?: number | null;
}

export interface ImageDocumentDetectedData {
  type?: "image_document_detected";
  found: boolean;
  top_left?: unknown[] | null;
  top_right?: unknown[] | null;
  bottom_right?: unknown[] | null;
  bottom_left?: unknown[] | null;
  confidence?: number;
  image_width?: number;
  image_height?: number;
}

export interface ImageEditCompleteData {
  type?: "image_edit_complete";
  op: string;
  file_id?: string | null;
  asset?: Record<string, unknown>;
}

export interface GeneratedImageFileItem {
  cloud_file_id: string;
  public_url?: string | null;
  mime?: string | null;
  width?: number | null;
  height?: number | null;
}

export interface ImageGenerateCompleteData {
  type?: "image_generate_complete";
  prompt?: string;
  model?: string | null;
  files?: GeneratedImageFileItem[];
}

export interface ImageOpStageData {
  type?: "image_op_stage";
  op: string;
  stage: string;
  detail?: string | null;
  source_id?: string | null;
  mask_id?: string | null;
}

export interface ImageOutputData {
  type?: "image_output";
  url: string;
  mime_type: string;
  file_id?: string | null;
  cdn_url?: string | null;
  download_url?: string | null;
}

export interface ImageStudioCommitCompleteData {
  type?: "image_studio_commit_complete";
  job_id: string;
  folder_path: string;
  saved_count?: number;
  failed_count?: number;
  result?: Record<string, unknown>;
}

export interface ImageStudioCommitItemData {
  type?: "image_studio_commit_item";
  job_id: string;
  preset_id: string;
  status: string;
  file_id?: string | null;
  filename?: string | null;
  file_path?: string | null;
  size?: number | null;
  public_url?: string | null;
  error?: string | null;
  completed?: number;
  total?: number;
}

export interface ImageStudioProcessCompleteData {
  type?: "image_studio_process_complete";
  job_id: string;
  result?: Record<string, unknown>;
}

export interface ImageStudioVariantData {
  type?: "image_studio_variant";
  job_id: string;
  preset_id: string;
  filename?: string | null;
  format?: string | null;
  width?: number | null;
  height?: number | null;
  quality?: number | null;
  size?: number | null;
  ephemeral_url?: string | null;
  expires_in?: number | null;
  compression_ratio?: number | null;
  notes?: string[];
  error?: string | null;
  completed?: number;
  total?: number;
}

export interface MediaSelectionJobProgressData {
  type?: "job.progress";
  job_id: string;
  at: string;
  totals?: Record<string, number>;
}

export interface KnowledgeBucketsDryRunDoneData {
  type?: "knowledge_buckets_dry_run_done";
  subject_user_id: string;
  organizations: number;
  items: number;
  seconds: number;
}

export interface KnowledgeBucketsFollowUpDoneData {
  type?: "knowledge_buckets_follow_up_done";
  kind: "merge" | "split";
  outcome: "existing" | "made" | "none";
  ids?: string[];
  why?: string;
}

export interface KnowledgeBucketsFollowUpProgressData {
  type?: "knowledge_buckets_follow_up_progress";
  kind: "merge" | "split";
  stage: "agent" | "entities" | "read" | "search" | "write";
  detail: string;
  done?: number;
  total?: number;
}

export interface KnowledgeBucketsOrgCounts {
  organization_id: string;
  counts: Record<string, number>;
}

export interface KnowledgeBucketsInventoryData {
  type?: "knowledge_buckets_inventory";
  subject_user_id: string;
  direct_counts: Record<string, number>;
  gathered_counts: Record<string, number>;
  counts_match: boolean;
  mismatches?: Record<string, number[]>;
  unmapped?: Record<string, number>;
  excluded?: Record<string, number>;
  per_org?: KnowledgeBucketsOrgCounts[];
}

export interface KnowledgeBucketCandidate {
  key: string;
  name: string;
  origin: string[];
  homes: number;
  by_group: Record<string, number>;
  weeks: number;
  score: number;
  score_all: number;
  entity_share: number;
  obvious: boolean;
  own_homes?: string[];
  outside: number;
  entity_kinds?: Record<string, number>;
  existing_scope?: KnowledgeBucketCandidateScope | null;
}

export interface KnowledgeBucketCandidateScope {
  kind: "scope" | "scope_type";
  id: string;
  name: string;
}

export interface KnowledgeBucketsOrgSpreadData {
  type?: "knowledge_buckets_org_spread";
  organization_id: string;
  homes: number;
  items_scored: number;
  items_with_text: number;
  items_with_entities: number;
  automation_skipped?: number;
  derived_files_skipped?: number;
  excluded?: Record<string, Record<string, number>>;
  candidates: number;
  scope_types: number;
  scopes: number;
  top_matching_scopes: number;
  top: KnowledgeBucketCandidate[];
}

export interface KnowledgeBucketsProgressData {
  type?: "knowledge_buckets_progress";
  stage: "homes" | "inventory" | "material" | "scopes" | "spread";
  detail: string;
  organization_id?: string | null;
  done?: number;
  total?: number;
}

export interface LegalSyncEventData {
  type?: "legal_sync_event";
  phase: string;
  run_id?: string | null;
  resource?: string | null;
  resources?: string[] | null;
  court_id?: string | null;
  court_ids?: string[] | null;
  cluster_id?: number | null;
  cluster_count?: number | null;
  rows?: number | null;
  court_rows?: number | null;
  total_rows?: number | null;
  rows_pumped?: number | null;
  rows_inserted?: number | null;
  bytes_received?: number | null;
  s3_key?: string | null;
  size_bytes?: number | null;
  dump_date?: string | null;
  bucket?: string | null;
  table?: string | null;
  target?: string | null;
  batch_size?: number | null;
  last_id?: number | null;
  reason?: string | null;
  per_court_counts?: Record<string, number> | null;
  per_court_errors?: Record<string, string> | null;
  per_cluster_errors?: Record<string, string> | null;
  per_resource_rows?: Record<string, number> | null;
  per_resource_errors?: Record<string, string> | null;
  error?: string | null;
}

export interface LibrarySyncClassifiedData {
  type?: "library.sync.classified";
  library_id: string;
  seq: number;
  at: string;
  classifications?: Record<string, JsonValue>[];
  from_cache: boolean;
}

export interface LibrarySyncCompletedData {
  type?: "library.sync.completed";
  library_id: string;
  seq: number;
  at: string;
  total_listed: number;
  elapsed_ms: number;
  listed_ms: number;
  quota_units_spent: number;
  metrics?: Record<string, JsonValue>;
  removed_count?: number;
  retire_refused?: boolean;
  skipped_by_reason?: Record<string, number>;
  skipped_total?: number;
  channel_link_error?: string | null;
}

export interface LibrarySyncFailedData {
  type?: "library.sync.failed";
  library_id: string;
  seq: number;
  at: string;
  code: string;
  message: string;
  partial_total?: number;
  retryable?: boolean;
}

export interface LibrarySyncListedData {
  type?: "library.sync.listed";
  library_id: string;
  seq: number;
  at: string;
  total_listed: number;
  elapsed_ms: number;
  deciding: number;
  skipped_by_reason?: Record<string, number>;
  skipped_total?: number;
}

export interface LibrarySyncPageData {
  type?: "library.sync.page";
  library_id: string;
  seq: number;
  at: string;
  page_index: number;
  page_size: number;
  cumulative: number;
  next_page_token_present: boolean;
  videos?: Record<string, JsonValue>[];
}

export interface LibrarySyncPersistedData {
  type?: "library.sync.persisted";
  library_id: string;
  seq: number;
  at: string;
  ids?: Record<string, string>;
  count: number;
}

export interface LibrarySyncStartedData {
  type?: "library.sync.started";
  library_id: string;
  seq: number;
  at: string;
  mode: string;
  expected_total?: number | null;
}

export interface LibrarySyncUnavailableData {
  type?: "library.sync.unavailable";
  library_id: string;
  seq: number;
  at: string;
  code: string;
  message: string;
  remedy?: string | null;
  partial_total?: number;
}

export interface MandateInputRequiredData {
  type?: "mandate_input_required";
  mandate_key: string;
  run_id: string;
  checkpoint_id?: string | null;
  inputs?: Record<string, JsonValue>[];
  message?: string;
}

export interface MandateRunData {
  type?: "mandate_run";
  mandate_key: string;
  door: string;
  request_id?: string | null;
  conversation_id?: string | null;
  mandate_id?: string | null;
  provenance?: string;
  replay_of?: string | null;
  rung?: string | null;
  holder_type?: string;
  holder_id?: string | null;
  holder_version_id?: string | null;
  holder_version_number?: number | null;
  holder_version_live?: boolean;
  model_id?: string | null;
  goal?: string | null;
  definition_version?: number | null;
  input_kind?: string | null;
  output_kind?: string | null;
  required_output_keys?: string[];
  placement?: Record<string, JsonValue> | null;
  placement_captured_at?: string;
  provision_applied?: boolean;
  holder_source?: string;
  started_by_request_id?: string | null;
  turn_author?: string | null;
  turn_input?: JsonValue;
  host_turn_reason?: string | null;
  turn_context?: Record<string, JsonValue> | null;
  earlier_frames?: Record<string, JsonValue>[];
}

export interface MandateTryResultData {
  type?: "mandate_try_result";
  mandate_key: string;
  result: Record<string, unknown>;
}

export interface MasterworkAgentBuildData {
  type?: "masterwork_agent_build";
  role: string;
  agent_name: string;
  message: string;
  build_id?: string | null;
  outcome?: string | null;
  agent_id?: string | null;
  reason?: string | null;
  proof_cases?: number;
}

export interface MasterworkAuditionOutcomeVerdictData {
  type?: "masterwork_audition_outcome_verdict";
  rulebook_id: string;
  case_id: string;
  run_scope: string;
  verdict: string;
  why?: string;
  summary?: string;
  judge_confidence?: number | null;
  dangerous_branch?: boolean;
  dangerous_branch_quote?: string;
  decisive_question_seq?: number | null;
  missed_cheaper_question?: string;
  disclosures?: number;
  asks?: number;
  cost_points?: number;
  risk_points?: number;
  quality_score?: number | null;
  weights?: Record<string, number>;
  vanilla_compared?: boolean;
  vanilla_verdict?: string | null;
  vanilla_answer?: string | null;
  vanilla_score?: number | null;
  vanilla_model?: string | null;
  vanilla_error?: string | null;
  beat_vanilla?: boolean | null;
  verdict_sentence?: string | null;
}

export interface MasterworkAuditionProgressData {
  type?: "masterwork_audition_progress";
  step: string;
  message: string;
}

export interface AuditionRuleFinding {
  rule_id: string;
  winner: string;
  note?: string;
}

export interface MasterworkAuditionVerdictData {
  type?: "masterwork_audition_verdict";
  rulebook_id: string;
  verdict: string;
  summary: string;
  findings?: AuditionRuleFinding[];
  gaps?: string[];
  gaps_captured?: number;
  rulebook_version?: number | null;
  quality_score?: number | null;
  judge_confidence?: number | null;
  vanilla_compared?: boolean;
  vanilla_score?: number | null;
  vanilla_verdict?: string | null;
  vanilla_findings?: AuditionRuleFinding[];
  vanilla_text?: string | null;
  vanilla_model?: string | null;
  vanilla_note?: string | null;
  vanilla_error?: string | null;
  beat_vanilla_rules?: number | null;
  lost_to_vanilla_rules?: number | null;
  vanilla_rules_compared?: number | null;
  verdict_sentence?: string | null;
}

export interface MasterworkBenchArmData {
  type?: "masterwork_bench_arm";
  rulebook_id: string;
  trial_id: string;
  arm: string;
  label: string;
  ran?: boolean;
  error?: string;
  cost_usd?: number;
  seconds?: number;
  priced?: boolean;
  model?: string;
  note?: string;
}

export interface MasterworkBenchProgressData {
  type?: "masterwork_bench_progress";
  rulebook_id: string;
  trial_id: string;
  stage: string;
  message: string;
  arm?: string;
}

export interface MasterworkBenchVerdictData {
  type?: "masterwork_bench_verdict";
  rulebook_id: string;
  trial_id: string;
  passed?: boolean;
  void?: boolean;
  not_scored?: boolean;
  void_reason?: string;
  not_scored_reason?: string;
  win_claimed?: string | null;
  win_rationale?: string;
  arm?: string;
  budget_multiple?: number | null;
  panel_winner?: string | null;
  panel_votes?: number;
  gt_in_pool?: boolean;
  gt_won?: boolean;
  c_cost_usd?: number | null;
  c_seconds?: number | null;
  total_cost_usd?: number;
  record_path?: string | null;
  report_path?: string | null;
  row_id?: string | null;
  stored?: boolean;
  storage_note?: string;
  headline?: string;
}

export interface MasterworkBuildCompleteData {
  type?: "masterwork_build_complete";
  workflow_id: string;
  name: string;
  masterwork_kind: string;
  rulebook_id: string;
  rulebook_slug: string;
  rulebook_version: number;
  agent_ids?: string[];
  submit_label?: string | null;
}

export interface MasterworkBuildProgressData {
  type?: "masterwork_build_progress";
  step: string;
  message: string;
  agent_id?: string | null;
  agent_name?: string | null;
}

export interface CheckupFinding {
  id: string;
  kind: string;
  target_rule_id?: string | null;
  proposed?: Record<string, unknown> | null;
  alternatives?: Record<string, unknown>[];
  reason?: string;
  evidence?: string;
  evidence_ref?: Record<string, unknown>;
  confidence?: number;
  source?: string;
  content_ir?: Record<string, unknown> | null;
}

export interface MasterworkCheckupCompleteData {
  type?: "masterwork_checkup_complete";
  rulebook_id: string;
  rulebook_version: number;
  findings?: CheckupFinding[];
  producers_run?: string[];
  producers_failed?: string[];
  evidence_rejected?: number;
  already_dismissed?: number;
  corpus_segments?: number;
  corpus_chars?: number;
  corpus_cleaned?: boolean;
  notes?: string[];
}

export interface MasterworkCheckupFindingData {
  type?: "masterwork_checkup_finding";
  rulebook_id: string;
  finding: CheckupFinding;
  content_ir?: Record<string, unknown> | null;
}

export interface MasterworkCheckupProgressData {
  type?: "masterwork_checkup_progress";
  step: string;
  message: string;
  producer?: string | null;
  findings_found?: number | null;
  findings_dropped?: number | null;
}

export interface MasterworkCorpusCleanedData {
  type?: "masterwork_corpus_cleaned";
  rulebook_id: string;
  rulebook_version: number;
  segments?: number;
  cleaned?: number;
  reused?: number;
  failed?: number;
  total_chars?: number;
  text?: string;
}

export interface MasterworkCorpusProgressData {
  type?: "masterwork_corpus_progress";
  step: string;
  message: string;
  segment_label?: string | null;
  segment_index?: number | null;
  total_segments?: number | null;
}

export interface MasterworkDumpResourceOutcome {
  kind: string;
  token?: string | null;
  id?: string | null;
  url?: string | null;
  source_key?: string | null;
  title?: string | null;
  status: string;
  rules_added?: number;
  duplicates?: number;
  error?: string | null;
  note?: string | null;
  already_distilled?: MasterworkSourceAlreadyDistilled | null;
  replaced_rules?: number;
}

export interface MasterworkSourceAlreadyDistilled {
  source: string;
  label?: string | null;
  rules?: number;
  draft_rules?: number;
  approved_rules?: number;
  run_ids?: string[];
  message: string;
  can_replace?: boolean;
}

export interface MasterworkDumpCompleteData {
  type?: "masterwork_dump_complete";
  rulebook_id: string;
  rulebook_version: number;
  added?: number;
  duplicates_skipped?: number;
  quotes_verified?: number;
  quotes_unverified?: number;
  resources?: MasterworkDumpResourceOutcome[];
  already_distilled?: MasterworkSourceAlreadyDistilled[];
  replaced_rules?: number;
}

export interface MasterworkDumpProgressData {
  type?: "masterwork_dump_progress";
  step: string;
  message: string;
  resource_index: number;
  resource_count: number;
  kind?: string | null;
  token?: string | null;
  id?: string | null;
  url?: string | null;
  title?: string | null;
  rules_added?: number | null;
  rules_added_total?: number;
}

export interface MasterworkSectionYield {
  index: number;
  label?: string;
  words?: number;
  chunks?: number;
  rules?: number;
  second_pass?: boolean;
}

export interface MasterworkIngestCompleteData {
  type?: "masterwork_ingest_complete";
  rulebook_id: string;
  rulebook_version: number;
  added?: number;
  duplicates_skipped?: number;
  quotes_verified?: number;
  quotes_unverified?: number;
  failed_chunks?: number;
  skipped_words?: number;
  packaging_set_aside?: number;
  followup_seed?: string | null;
  already_distilled?: MasterworkSourceAlreadyDistilled[];
  replaced_rules?: number;
  narrowed_rules?: number;
  corpus_item_id?: string | null;
  sealed?: boolean;
  timeline_steps?: number;
  role?: string | null;
  timeline?: Record<string, JsonValue> | null;
  sections?: MasterworkSectionYield[];
}

export interface MasterworkIngestProgressData {
  type?: "masterwork_ingest_progress";
  step: string;
  message: string;
  chunk_index?: number | null;
  total_chunks?: number | null;
  rules_found?: number | null;
}

export interface MasterworkPairwiseArmFaithfulness {
  side: string;
  label: string;
  rulebook_id: string;
  verdict: string;
  reasoning?: string;
  confidence?: number | null;
  departures?: MasterworkPairwiseRuleNote[];
  honored_rule_ids?: string[];
}

export interface MasterworkPairwiseDifference {
  aspect: string;
  one_does?: string;
  two_does?: string;
  matters?: string;
}

export interface MasterworkPairwiseRuleNote {
  rule_id: string;
  winner: string;
  note?: string;
}

export interface MasterworkPairwiseVerdictData {
  type?: "masterwork_pairwise_verdict";
  rulebook_id: string;
  mode: string;
  candidate_one_label: string;
  candidate_two_label: string;
  preferred?: string | null;
  preferred_label?: string | null;
  judge_verdict?: string;
  judge_confidence?: number | null;
  summary?: string;
  differences?: MasterworkPairwiseDifference[];
  rule_notes?: MasterworkPairwiseRuleNote[];
  faithfulness?: MasterworkPairwiseArmFaithfulness[];
  blind_key?: Record<string, string>;
  blind_key_sealed_at?: string | null;
  verdict_sentence?: string | null;
}

export interface MasterworkProbeRoundData {
  type?: "masterwork_probe_round";
  rulebook_id: string;
  rulebook_version?: number;
  round_index?: number;
  round_count?: number;
  done?: boolean;
  done_reason?: string;
  example_title?: string;
  example_body?: string;
  probe_label?: string;
  rules_added?: number;
  rule_ids?: string[];
  duplicates_skipped?: number;
  quotes_verified?: number;
  quotes_unverified?: number;
  already_distilled?: MasterworkSourceAlreadyDistilled[];
  answered_rounds?: number;
}

export interface MasterworkRunData {
  type?: "masterwork_run";
  run_id: string;
  rulebook_id: string;
  operation: string;
  label?: string | null;
}

export interface MasterworkRunCancelledData {
  type?: "masterwork_run_cancelled";
  run_id: string;
  error?: Record<string, unknown> | null;
}

export interface MasterworkRunFailedData {
  type?: "masterwork_run_failed";
  run_id: string;
  error?: Record<string, unknown> | null;
}

export interface MasterworkRunSnapshotData {
  type?: "masterwork_run_snapshot";
  run_id: string;
  rulebook_id: string;
  operation: string;
  label?: string | null;
  status: string;
  live?: boolean;
  error?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  completed_at?: string | null;
}

export interface MasterworkSealedCaseDisclosureData {
  type?: "masterwork_sealed_case_disclosure";
  case_id: string;
  run_scope: string;
  seq: number;
  event?: string;
  question?: string;
  answer?: string;
  found?: boolean;
  cost?: string | null;
  risk?: string | null;
  asked_kind?: string | null;
  disclosures_so_far?: number;
}

export interface MasterworkShortlistItem {
  key: string;
  reason?: string;
}

export interface MasterworkShortlistData {
  type?: "masterwork_shortlist";
  selected?: MasterworkShortlistItem[];
  considered?: number;
}

export interface MasterworkSortCase {
  id: string;
  text: string;
  note?: string;
}

export interface MasterworkSortCasesReadyData {
  type?: "masterwork_sort_cases_ready";
  rulebook_id: string;
  cases?: MasterworkSortCase[];
  requested?: number;
  repeats_dropped?: number;
  pile_count?: number;
  boundary_questions?: number;
  voice_default_on?: boolean;
}

export interface MasterworkTeachBackRoundData {
  type?: "masterwork_teach_back_round";
  rulebook_id: string;
  rulebook_version?: number;
  run_id?: string;
  round_index?: number;
  round_count?: number;
  done?: boolean;
  done_reason?: string;
  subject?: string;
  explanation?: string;
  basis?: string;
  rule_ids_cited?: string[];
  uncertain_part?: string;
  distilled_round?: number;
  rules_added?: number;
  rule_ids?: string[];
  duplicates_skipped?: number;
  quotes_verified?: number;
  quotes_unverified?: number;
  signed_rule_ids?: string[];
  signature_note?: string;
  already_distilled?: MasterworkSourceAlreadyDistilled[];
  answered_rounds?: number;
}

export interface MasterworkTriad {
  id: string;
  prompt: string;
  items?: MasterworkTriadItem[];
  mode?: string;
}

export interface MasterworkTriadItem {
  key: string;
  text: string;
  note?: string;
}

export interface MasterworkTriadsReadyData {
  type?: "masterwork_triads_ready";
  rulebook_id: string;
  triads?: MasterworkTriad[];
  mode?: string;
  requested?: number;
  repeats_dropped?: number;
  voice_default_on?: boolean;
}

export interface MasterworkTriageDecision {
  rule_id: string;
  name?: string;
  verdict?: string;
  reason?: string;
  statement?: string;
}

export interface MasterworkTriageCompleteData {
  type?: "masterwork_triage_complete";
  rulebook_id: string;
  rulebook_version: number;
  drafts_considered?: number;
  retired?: number;
  kept?: number;
  rewritten?: number;
  refused?: string[];
  failed_batches?: number;
  drafts_unreviewed?: number;
  dry_run?: boolean;
  decisions?: MasterworkTriageDecision[];
}

export interface MasterworkTriageProgressData {
  type?: "masterwork_triage_progress";
  step: string;
  message: string;
  batch_index?: number | null;
  batch_count?: number | null;
  drafts_total?: number;
  retired?: number;
  kept?: number;
  rewritten?: number;
}

export interface AudioBlock {
  origin: "external" | "matrx";
  file_id?: string | null;
  published_to_web?: boolean | null;
  shown_to?: "everyone" | "everyone_on_ai_matrx" | "my_team" | "only_me" | null;
  visibility?: "internal" | "link" | "personal" | "public" | null;
  cdn_url?: string | null;
  download_url?: string | null;
  parent_file_id?: string | null;
  derivation_kind?: string | null;
  external_url?: string | null;
  source_label?: string | null;
  base64?: string | null;
  mime_type?: string | null;
  file_name?: string | null;
  size_bytes?: number | null;
  status?: "complete" | "error" | "streaming";
  progress?: number | null;
  error_message?: string | null;
  metadata?: Record<string, JsonValue>;
  kind?: "audio";
  duration_ms?: number | null;
  transcript?: string | null;
}

export interface DocumentBlock {
  origin: "external" | "matrx";
  file_id?: string | null;
  published_to_web?: boolean | null;
  shown_to?: "everyone" | "everyone_on_ai_matrx" | "my_team" | "only_me" | null;
  visibility?: "internal" | "link" | "personal" | "public" | null;
  cdn_url?: string | null;
  download_url?: string | null;
  parent_file_id?: string | null;
  derivation_kind?: string | null;
  external_url?: string | null;
  source_label?: string | null;
  base64?: string | null;
  mime_type?: string | null;
  file_name?: string | null;
  size_bytes?: number | null;
  status?: "complete" | "error" | "streaming";
  progress?: number | null;
  error_message?: string | null;
  metadata?: Record<string, JsonValue>;
  kind?: "document";
  page_count?: number | null;
  page1_url?: string | null;
}

export interface ImageBlock {
  origin: "external" | "matrx";
  file_id?: string | null;
  published_to_web?: boolean | null;
  shown_to?: "everyone" | "everyone_on_ai_matrx" | "my_team" | "only_me" | null;
  visibility?: "internal" | "link" | "personal" | "public" | null;
  cdn_url?: string | null;
  download_url?: string | null;
  parent_file_id?: string | null;
  derivation_kind?: string | null;
  external_url?: string | null;
  source_label?: string | null;
  base64?: string | null;
  mime_type?: string | null;
  file_name?: string | null;
  size_bytes?: number | null;
  status?: "complete" | "error" | "streaming";
  progress?: number | null;
  error_message?: string | null;
  metadata?: Record<string, JsonValue>;
  kind?: "image";
  width?: number | null;
  height?: number | null;
  vision_class?: string | null;
}

export interface VideoBlock {
  origin: "external" | "matrx";
  file_id?: string | null;
  published_to_web?: boolean | null;
  shown_to?: "everyone" | "everyone_on_ai_matrx" | "my_team" | "only_me" | null;
  visibility?: "internal" | "link" | "personal" | "public" | null;
  cdn_url?: string | null;
  download_url?: string | null;
  parent_file_id?: string | null;
  derivation_kind?: string | null;
  external_url?: string | null;
  source_label?: string | null;
  base64?: string | null;
  mime_type?: string | null;
  file_name?: string | null;
  size_bytes?: number | null;
  status?: "complete" | "error" | "streaming";
  progress?: number | null;
  error_message?: string | null;
  metadata?: Record<string, JsonValue>;
  kind?: "video";
  width?: number | null;
  height?: number | null;
  duration_ms?: number | null;
  poster_url?: string | null;
}

export interface YouTubeBlock {
  origin?: "external";
  file_id?: string | null;
  published_to_web?: boolean | null;
  shown_to?: "everyone" | "everyone_on_ai_matrx" | "my_team" | "only_me" | null;
  visibility?: "internal" | "link" | "personal" | "public" | null;
  cdn_url?: string | null;
  download_url?: string | null;
  parent_file_id?: string | null;
  derivation_kind?: string | null;
  external_url?: string | null;
  source_label?: string | null;
  base64?: string | null;
  mime_type?: string | null;
  file_name?: string | null;
  size_bytes?: number | null;
  status?: "complete" | "error" | "streaming";
  progress?: number | null;
  error_message?: string | null;
  metadata?: Record<string, JsonValue>;
  kind?: "youtube";
  video_id?: string | null;
}

export interface MediaBlockData {
  type?: "media_block";
  block: ImageBlock | VideoBlock | AudioBlock | DocumentBlock | YouTubeBlock;
}

export interface MediaNoticeData {
  type?: "media_notice";
  media_kind: string;
  action: "converted" | "dropped" | "extracted" | "transcribed";
  user_message: string;
  system_message?: string;
  provider?: string | null;
  model?: string | null;
  metadata?: Record<string, unknown>;
}

export interface MediaResearchAnchor {
  title?: string | null;
  url: string;
  published_at?: string | null;
}

export interface MediaResearchProfile {
  name: string;
  outlet?: string | null;
  url?: string | null;
  published_at?: string | null;
}

export interface MediaResearchRow {
  party_id: string;
  name: string;
  outlet?: string | null;
  status: "cut" | "fit" | "research_needed" | "soft_fit";
  rank?: number;
  why_them?: string;
  anchor?: MediaResearchAnchor | null;
  pitch_note?: string | null;
  cut_reason?: string | null;
  contact_state?: "quarantined" | "unresolved" | "verified";
  contact_address?: string | null;
  contact_note?: string | null;
  first_wave?: boolean;
  fit_check?: string | null;
  on_list?: boolean;
}

export interface MediaResearchProgressData {
  type?: "media_research_progress";
  run_key: string;
  stage: "approved" | "contacts" | "first_wave" | "judge" | "resolve" | "save" | "search";
  percent: number;
  says: string;
  found?: number;
  target?: number;
  judged?: number;
  new_profiles?: MediaResearchProfile[];
  rows?: MediaResearchRow[];
}

export interface MediaResearchSummaryData {
  requested: number;
  research_target: number;
  multiplier: number;
  multiplier_reason: string;
  resolved?: number;
  judged?: number;
  first_wave?: number;
  added_to_list?: number;
  already_on_list?: number;
  gaps?: string[];
}

export interface MediaResearchResultData {
  type?: "media_research_result";
  run_key: string;
  list_id: string;
  status: "complete" | "partial";
  reused?: boolean;
  says: string;
  summary: MediaResearchSummaryData;
  rows?: MediaResearchRow[];
  cuts?: MediaResearchRow[];
  problems?: string[];
  next_action?: string;
}

export interface MemoryBufferSpawnedData {
  type?: "memory_buffer_spawned";
  conversation_id: string;
  kind?: "observer" | "reflector";
}

export interface MemoryContextInjectedData {
  type?: "memory_context_injected";
  conversation_id: string;
  observation_chars?: number;
}

export interface MemoryErrorData {
  type?: "memory_error";
  conversation_id: string;
  phase?: string;
  error?: string;
  model?: string | null;
}

export interface MemoryObserverCompletedData {
  type?: "memory_observer_completed";
  conversation_id: string;
  model?: string | null;
  input_tokens?: number;
  output_tokens?: number;
  cost?: number;
  duration_ms?: number | null;
}

export interface MemoryReflectorCompletedData {
  type?: "memory_reflector_completed";
  conversation_id: string;
  model?: string | null;
  input_tokens?: number;
  output_tokens?: number;
  cost?: number;
  duration_ms?: number | null;
}

export interface PartialImageData {
  type?: "partial_image";
  b64_json: string;
  partial_index: number;
  progress?: number;
  mime_type?: string;
}

export interface PdfPageClassificationItem {
  page_number: number;
  page_class: string;
  confidence: number;
  indicators?: string[];
}

export interface PdfClassifyCompleteData {
  type?: "pdf_classify_complete";
  page_count: number;
  pages?: PdfPageClassificationItem[];
  classifier_version?: string;
}

export interface PdfCleanStartedData {
  type?: "pdf_clean_started";
  mode?: "aggregate" | "per_page";
  doc_id: string;
  total_pages?: number;
}

export interface PdfExtractCompleteData {
  type?: "pdf_extract_complete";
  filename?: string | null;
  page_count: number;
  total_pages?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  ocr_pages: number;
  total_chars: number;
  text_content: string;
  file_id?: string | null;
}

export interface PdfExtractStartedData {
  type?: "pdf_extract_started";
  filename?: string | null;
  total_pages: number;
}

export interface PdfPageClassifiedData {
  type?: "pdf_page_classified";
  page_number: number;
  total_pages: number;
  page_class: string;
  confidence: number;
  indicators?: string[];
}

export interface PdfPageExtractedData {
  type?: "pdf_page_extracted";
  page_number: number;
  total_pages: number;
  extraction_method: string;
  char_count: number;
  preview?: string;
}

export interface PdfPipelineResultData {
  type?: "pdf_pipeline_result";
  raw_text?: string | null;
  page_count?: number;
  chunks?: string[] | null;
  ai_processed?: Record<string, unknown>[] | null;
  cloud_uri?: string | null;
  file_id?: string | null;
}

export interface PdfReadingOrderBlockItem {
  block_index: number;
  column_index: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  text: string;
}

export interface PdfReadingOrderPageItem {
  page_number: number;
  column_count: number;
  blocks_in_order?: PdfReadingOrderBlockItem[];
}

export interface PdfReadingOrderCompleteData {
  type?: "pdf_reading_order_complete";
  page_count: number;
  pages?: PdfReadingOrderPageItem[];
}

export interface PdfReadingOrderPageData {
  type?: "pdf_reading_order_page";
  page_number: number;
  total_pages: number;
  column_count: number;
  block_count: number;
  preview?: string;
}

export interface PdfRepeatedRegionBboxItem {
  page_number: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  raw_text?: string;
}

export interface PdfRepeatedRegionItem {
  region_id: string;
  kind: string;
  text_template: string;
  pages?: number[];
  bbox_per_page?: PdfRepeatedRegionBboxItem[];
  confidence: number;
}

export interface PdfRepeatedRegionsCompleteData {
  type?: "pdf_repeated_regions_complete";
  page_count: number;
  regions?: PdfRepeatedRegionItem[];
  detector_version?: string;
}

export interface PdfRepeatedRegionsProgressData {
  type?: "pdf_repeated_regions_progress";
  stage: "detect" | "extract_text" | "strip";
  page_number: number;
  total_pages: number;
}

export interface PdfRepeatedRegionsStripCompleteData {
  type?: "pdf_repeated_regions_strip_complete";
  page_count: number;
  pages_text?: string[];
  regions?: PdfRepeatedRegionItem[];
  detector_version?: string;
  stripped_region_ids?: string[];
}

export interface PdfTableExtractedData {
  type?: "pdf_table_extracted";
  page_number: number;
  total_pages: number;
  table_index: number;
  row_count: number;
  column_count: number;
  markdown_preview?: string;
}

export interface PdfExtractedTableItem {
  page_number: number;
  table_index: number;
  bbox: PdfTableBboxItem;
  row_count: number;
  column_count: number;
  header?: (string | null)[];
  rows?: (string | null)[][];
  markdown?: string;
}

export interface PdfTableBboxItem {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface PdfTablesCompleteData {
  type?: "pdf_tables_complete";
  page_count: number;
  table_count: number;
  tables?: PdfExtractedTableItem[];
  detector?: string;
  detector_version?: string;
}

export interface PdfTablesPageData {
  type?: "pdf_tables_page";
  page_number: number;
  total_pages: number;
  tables_found: number;
}

export interface PdfTablesStartedData {
  type?: "pdf_tables_started";
  filename?: string | null;
  total_pages: number;
}

export interface PlanBriefDraftData {
  type?: "plan_brief_draft";
  node_id: string;
  route?: string;
  brief?: string[];
  angle?: string;
  must_not_cover?: string[];
  concerns?: string[];
  suggested_word_count?: number | null;
  accepted?: boolean;
}

export interface PlanCmsFillPreviewData {
  type?: "plan_cms_fill_preview";
  node_id: string;
  page_id: string;
  route?: string;
  title?: string;
  html?: string;
  css?: string;
  meta_title?: string;
  meta_description?: string;
  model?: string;
  wrote?: boolean;
  is_published?: boolean;
  write_target?: "draft" | "live";
  global_css?: string;
  header_html?: string;
  footer_html?: string;
}

export interface PlanDeepenResultData {
  type?: "plan_deepen_result";
  node_id: string;
  route?: string;
  brief_lines?: number;
  sources_attached?: number;
}

export interface PlanGenAppliedData {
  type?: "plan_gen_applied";
  site_id: string;
  created: number;
  existing: number;
  failed: number;
  errors?: string[];
  dry_run?: boolean;
}

export interface PlanGenCandidateData {
  type?: "plan_gen_candidate";
  angle: string;
  node_count: number;
  rationale?: string;
}

export interface PlanGenMergedData {
  type?: "plan_gen_merged";
  node_count: number;
  summary?: string;
  candidates_used?: number;
}

export interface PlanGenStartedData {
  type?: "plan_gen_started";
  site_id: string;
  domain: string;
  angles?: string[];
  keyword_count?: number;
}

export interface PlanPageStepResultData {
  type?: "plan_page_step_result";
  node_id: string;
  step: string;
  route?: string;
  artifact_id?: string;
  run_id?: string;
  summary?: string;
}

export interface PlanSetupAgentResultData {
  type?: "plan_setup_agent_result";
  site_id: string;
  kind: string;
  run_id: string;
  item_count?: number;
  summary?: string;
  model_id?: string | null;
}

export interface PodcastAssetEvent {
  type?: "podcast_asset";
  asset_kind: "image" | "video";
  index: number;
  url?: string;
  prompt?: string;
  success?: boolean;
  error?: string | null;
  note?: string | null;
}

export interface PodcastAssetGenStartedEvent {
  type?: "podcast_asset_gen_started";
  run_id: string;
  asset_kind: "image" | "video";
  slot: number;
  prompt?: string;
  model_alias?: string | null;
  is_manual?: boolean;
}

export interface PodcastAssetResultEvent {
  type?: "podcast_asset_result";
  run_id: string;
  asset_id?: string | null;
  asset_kind: "image" | "video";
  slot: number;
  status: string;
  url?: string | null;
  file_id?: string | null;
  prompt?: string | null;
  model_alias?: string | null;
  is_manual?: boolean;
  error?: string | null;
}

export interface PodcastCompleteEvent {
  type?: "podcast_complete";
  show_id: string;
  success: boolean;
  episode_id?: string | null;
  episode_slug?: string | null;
  script?: string;
  audio_url?: string;
  title?: string;
  description?: string;
  image_urls?: string[];
  video_urls?: string[];
  audio_file_id?: string;
  image_file_ids?: string[];
  video_file_ids?: string[];
  official_video_file_id?: string;
  official_video_url?: string;
  official_video_error?: string;
  run_mode?: string;
  host_count?: number;
  speakers?: Record<string, unknown>[];
  error?: string | null;
}

export interface PodcastMetadataEvent {
  type?: "podcast_metadata";
  title?: string;
  description?: string;
  image_descriptions?: string[];
  video_descriptions?: string[];
}

export interface PodcastOfficialVideoEvent {
  type?: "podcast_official_video";
  url?: string;
  success?: boolean;
  error?: string | null;
}

export interface PodcastRunEvent {
  type?: "podcast_run";
  run_id?: string;
  total?: number;
}

export interface PodcastStageEvent {
  type?: "podcast_stage";
  stage: string;
  label?: string;
  success: boolean;
  output?: string;
  error?: string | null;
  step?: number;
  total?: number;
}

export interface PodcastStageStartedEvent {
  type?: "podcast_stage_started";
  stage: string;
  label?: string;
  step?: number;
  total?: number;
}

export interface PodcastTickEvent {
  type?: "podcast_tick";
  stage: string;
  label?: string;
  elapsed_seconds?: number;
  step?: number;
  total?: number;
}

export interface ProofEvaluatedData {
  type?: "proof_evaluated";
  run_id: string;
  proof_id: string;
  title: string;
  status: "failed" | "passed" | "skipped";
  required?: boolean;
  detail?: string;
  observed?: Record<string, JsonValue>;
}

export interface ProofRunCompletedData {
  type?: "proof_run_completed";
  run_id: string;
  slug: string;
  mode: "live" | "replay";
  verdict: "fail" | "inconclusive" | "pass";
  strength: "live_receipts" | "replay_only";
  summary?: string;
  passed?: number;
  failed?: number;
  skipped?: number;
  cost_usd?: number;
  total_tokens?: number;
  provider_calls?: number;
  duration_ms?: number;
}

export interface ProofRunSkippedData {
  type?: "proof_run_skipped";
  slug: string;
  reason: string;
  month_to_date_usd?: number;
}

export interface ProofRunStartedData {
  type?: "proof_run_started";
  run_id: string;
  slug: string;
  label?: string;
  mode: "live" | "replay";
  gate_reason?: string;
  conversation_id?: string | null;
}

export interface ProofRunStepData {
  type?: "proof_run_step";
  run_id: string;
  message: string;
  step?: number;
}

export interface RagVerifyClaimsData {
  type?: "rag_verify_claims";
  claims?: string[];
  count: number;
  judge_model: string;
}

export interface RagVerifyClaimItem {
  claim: string;
  verdict: string;
  confidence: number;
  supporting_chunk_ids?: string[];
  reasoning?: string;
}

export interface RagVerifyResultData {
  type?: "rag_verify_result";
  claims?: RagVerifyClaimItem[];
  overall_faithfulness: number;
  judge_model: string;
  latency_ms: number;
}

export interface RagVerifyVerdictData {
  type?: "rag_verify_verdict";
  index: number;
  claim: string;
  verdict: string;
  confidence: number;
  supporting_chunk_ids?: string[];
  reasoning?: string;
}

export interface ScrapeBatchCompleteData {
  type?: "scrape_batch_complete";
  total_scraped: number;
}

export interface SearchErrorData {
  type?: "search_error";
  metadata?: Record<string, unknown>;
  error: string;
}

export interface SearchResultItem {
  [key: string]: unknown;
  url?: string;
  title?: string;
  snippet?: string;
  published?: string | null;
  source?: string | null;
}

export interface SearchResultsData {
  type?: "search_results";
  metadata?: Record<string, unknown>;
  results?: SearchResultItem[];
}

export interface SocialResultEvent {
  type?: "social_result";
  operation: "ingest_post" | "ingest_profile" | "refresh" | "track";
  result: Record<string, JsonValue>;
}

export interface SocialStageEvent {
  type?: "social_stage";
  operation: "ingest_post" | "ingest_profile" | "refresh" | "track";
  stage: string;
  label: string;
  current?: number;
  total?: number;
}

export interface LandedSourceRefData {
  envelope?: "resource_ref";
  resource_type: "file" | "processed_document";
  resource_id: string;
  label?: string | null;
}

export interface LandingNoticeData {
  code: string;
  message: string;
  remedy?: string;
}

export interface SourceLandUrlResultData {
  type?: "source_land_url_result";
  kind: "file" | "web_page" | "youtube";
  url: string;
  canonical_url?: string | null;
  file_id?: string | null;
  processed_document_id?: string | null;
  reused_existing?: boolean;
  mime_type?: string | null;
  name: string;
  ref: LandedSourceRefData;
  notices?: LandingNoticeData[];
}

export interface SourceLandUrlStageData {
  type?: "source_land_url_stage";
  stage: "fetching" | "landing" | "scraping" | "transcribing";
  url: string;
  detail?: string;
}

export interface StructuredInputFailure {
  url?: string;
  ref?: string;
  reason?: string;
}

export interface StructuredInputWarningData {
  type?: "structured_input_warning";
  block_type: string;
  failures?: StructuredInputFailure[];
}

export interface VaultPasswordHealthDiscoveryReference {
  request_id: string;
  execution_id: string;
}

export interface VaultPasswordHealthDiscoveryPageData {
  type?: "vault_password_health_discovery_page";
  references: VaultPasswordHealthDiscoveryReference[];
  continuation?: string | null;
}

export interface VaultPasswordHealthProgressData {
  type?: "vault_password_health_progress";
  request_id: string;
  execution_id: string;
  checked: number;
  skipped: number;
}

export interface VaultPasswordHealthScanReferenceData {
  type?: "vault_password_health_scan_reference";
  request_id: string;
  execution_id: string;
  resumed_from_execution_id?: string | null;
  status_path: string;
  events_path: string;
  rejoin_path: string;
  result_path: string;
  resume_path: string;
}

export interface VideoOutputData {
  type?: "video_output";
  url: string;
  mime_type: string;
  file_id?: string | null;
  cdn_url?: string | null;
  download_url?: string | null;
}

export interface WorkflowNodeTestResultData {
  type?: "workflow_node_test_result";
  success: boolean;
  duration_ms: number;
  node_id: string;
  spec_type: string;
  output?: Record<string, unknown> | null;
  error_type?: string | null;
  error_message?: string | null;
}

export interface WorkflowStepData {
  type?: "workflow_step";
  step_name: string;
  status: string;
  data?: Record<string, unknown>;
}

export interface YouTubeTranscriptSourceData {
  type?: "youtube_transcript_source";
  processed_document_id: string;
  transcript_id: string;
  video_id: string;
  title: string;
  segment_count: number;
  reused_existing: boolean;
}

export type TypedDataPayload =
  | AgentStudioFromChatProgressData
  | AgentStudioFromChatResultData
  | AgentTestProgressEvent
  | AgentTestResultEvent
  | AgentTestRunStartedEvent
  | AgentTestRunStatusEvent
  | AssignmentProgressData
  | AudioOutputData
  | AudioStreamChunkData
  | AudioStreamEndData
  | CategorizationResultData
  | ClaudeManagedSdkMessageData
  | ClaudeManagedWarningData
  | CmsLogoFoundData
  | ContextChangedData
  | ContextConflictData
  | ContextDeltaData
  | ContextPersistFailedData
  | ContextPersistedData
  | ContextReceiptData
  | ConversationIdData
  | ConversationLabeledData
  | CutoverCopyAgainProgressData
  | CutoverCopyAgainReportData
  | CutoverFinalSwitchCopyAgainResultData
  | CutoverFinalSwitchResultData
  | CutoverFinalSwitchStageData
  | DecisionAnswersData
  | DecisionReferenceItemData
  | DecisionReferenceItemFailedData
  | DecisionReferenceRunDoneData
  | DecisionReferenceRunStartedData
  | DictionaryPublishCompleteData
  | EsignBulkSendCreatedData
  | EsignBulkSendResultData
  | EsignBulkSendRowData
  | EsignBulkSendRowsInvalidData
  | ExtractionIndexCompleteData
  | ExtractionIndexProgressData
  | FetchResultsData
  | FileAnalysisCompleteData
  | FileAnalysisStartedData
  | FileDetectorCompletedData
  | FileSearchCompleteData
  | FileSearchPageData
  | FileSearchStartedData
  | FirstTurnFilledData
  | FunctionResultData
  | ImageDocumentDetectedData
  | ImageEditCompleteData
  | ImageGenerateCompleteData
  | ImageOpStageData
  | ImageOutputData
  | ImageStudioCommitCompleteData
  | ImageStudioCommitItemData
  | ImageStudioProcessCompleteData
  | ImageStudioVariantData
  | KnowledgeBucketsDryRunDoneData
  | KnowledgeBucketsFollowUpDoneData
  | KnowledgeBucketsFollowUpProgressData
  | KnowledgeBucketsInventoryData
  | KnowledgeBucketsOrgSpreadData
  | KnowledgeBucketsProgressData
  | LegalSyncEventData
  | LibrarySyncClassifiedData
  | LibrarySyncCompletedData
  | LibrarySyncFailedData
  | LibrarySyncListedData
  | LibrarySyncPageData
  | LibrarySyncPersistedData
  | LibrarySyncStartedData
  | LibrarySyncUnavailableData
  | MandateInputRequiredData
  | MandateRunData
  | MandateTryResultData
  | MasterworkAgentBuildData
  | MasterworkAuditionOutcomeVerdictData
  | MasterworkAuditionProgressData
  | MasterworkAuditionVerdictData
  | MasterworkBenchArmData
  | MasterworkBenchProgressData
  | MasterworkBenchVerdictData
  | MasterworkBuildCompleteData
  | MasterworkBuildProgressData
  | MasterworkCheckupCompleteData
  | MasterworkCheckupFindingData
  | MasterworkCheckupProgressData
  | MasterworkCorpusCleanedData
  | MasterworkCorpusProgressData
  | MasterworkDumpCompleteData
  | MasterworkDumpProgressData
  | MasterworkIngestCompleteData
  | MasterworkIngestProgressData
  | MasterworkPairwiseVerdictData
  | MasterworkProbeRoundData
  | MasterworkRunCancelledData
  | MasterworkRunData
  | MasterworkRunFailedData
  | MasterworkRunSnapshotData
  | MasterworkSealedCaseDisclosureData
  | MasterworkShortlistData
  | MasterworkSortCasesReadyData
  | MasterworkTeachBackRoundData
  | MasterworkTriadsReadyData
  | MasterworkTriageCompleteData
  | MasterworkTriageProgressData
  | MediaBlockData
  | MediaNoticeData
  | MediaResearchProgressData
  | MediaResearchResultData
  | MediaSelectionJobProgressData
  | MemoryBufferSpawnedData
  | MemoryContextInjectedData
  | MemoryErrorData
  | MemoryObserverCompletedData
  | MemoryReflectorCompletedData
  | PartialImageData
  | PdfClassifyCompleteData
  | PdfCleanStartedData
  | PdfExtractCompleteData
  | PdfExtractStartedData
  | PdfPageClassifiedData
  | PdfPageExtractedData
  | PdfPipelineResultData
  | PdfReadingOrderCompleteData
  | PdfReadingOrderPageData
  | PdfRepeatedRegionsCompleteData
  | PdfRepeatedRegionsProgressData
  | PdfRepeatedRegionsStripCompleteData
  | PdfTableExtractedData
  | PdfTablesCompleteData
  | PdfTablesPageData
  | PdfTablesStartedData
  | PlanBriefDraftData
  | PlanCmsFillPreviewData
  | PlanDeepenResultData
  | PlanGenAppliedData
  | PlanGenCandidateData
  | PlanGenMergedData
  | PlanGenStartedData
  | PlanPageStepResultData
  | PlanSetupAgentResultData
  | PodcastAssetEvent
  | PodcastAssetGenStartedEvent
  | PodcastAssetResultEvent
  | PodcastCompleteEvent
  | PodcastMetadataEvent
  | PodcastOfficialVideoEvent
  | PodcastRunEvent
  | PodcastStageEvent
  | PodcastStageStartedEvent
  | PodcastTickEvent
  | ProofEvaluatedData
  | ProofRunCompletedData
  | ProofRunSkippedData
  | ProofRunStartedData
  | ProofRunStepData
  | QuestionnaireDisplayData
  | RagVerifyClaimsData
  | RagVerifyResultData
  | RagVerifyVerdictData
  | ScrapeBatchCompleteData
  | SearchErrorData
  | SearchResultsData
  | SocialResultEvent
  | SocialStageEvent
  | SourceLandUrlResultData
  | SourceLandUrlStageData
  | StructuredInputWarningData
  | VaultPasswordHealthDiscoveryPageData
  | VaultPasswordHealthProgressData
  | VaultPasswordHealthScanReferenceData
  | VideoOutputData
  | WorkflowNodeTestResultData
  | WorkflowStepData
  | YouTubeTranscriptSourceData;

/** Fallback for data events whose `type` isn't in TypedDataPayload. */
export interface UntypedDataPayload {
  [key: string]: unknown;
  type: string;
}

// --- Conversation Value Store Events (kind-discriminated data events) ---

export interface ValueDescriptor {
  key: string;
  description: string;
  kind: string;
  chars: number;
  truncated?: boolean;
  preview?: string;
  json_keys?: string[] | null;
  fence: string;
}

export interface ValueStoredEvent {
  kind?: "value_store.stored";
  conversation_id: string;
  descriptor: ValueDescriptor;
  source_agent_id?: string | null;
  source_call_id?: string | null;
}

export interface ContextGroomedEvent {
  kind?: "value_store.groomed";
  conversation_id: string;
  stubbed_keys?: string[];
  retained_keys?: string[];
  source: string;
}

export type ValueStoreDataEvent = ValueStoredEvent | ContextGroomedEvent;

/** Narrows a `data` event payload to ValueStoredEvent (a sub-agent result landed in the store). */
export function isValueStoredEvent(value: unknown): value is ValueStoredEvent {
  return typeof value === "object" && value !== null
    && (value as { kind?: unknown }).kind === "value_store.stored";
}

/** Narrows a `data` event payload to ContextGroomedEvent (model-view groom stamps applied). */
export function isContextGroomedEvent(value: unknown): value is ContextGroomedEvent {
  return typeof value === "object" && value !== null
    && (value as { kind?: unknown }).kind === "value_store.groomed";
}

// --- SEO Streamed Result Models (kind-discriminated data events) ---

export interface KeywordClassifyResult {
  result_kind?: "keywords.classify";
  eligible?: number;
  batches?: number;
  updated?: number;
  skipped_error?: number;
  missing_keyword_ids?: string[];
  facet_dimensions?: string[];
  facet_dimensions_skipped?: string[];
  facet_rows_written?: number;
  rejected_unknown_value?: string[];
}

export interface KeywordResearchArtifact {
  __kind?: "keyword_relationship_research";
  primary_keyword: string;
  keyword_lists: KeywordResearchList[];
}

export interface KeywordResearchIngestSummary {
  primary_keyword_ids?: string[];
  keywords_created?: number;
  keywords_already_existed?: number;
  edges_written?: number;
  edges_skipped_rejected?: number;
  edges_skipped_self?: number;
  site_keyword_values_created?: number;
}

export interface KeywordResearchList {
  __kind?: "keyword_list";
  label: string;
  keywords: string[];
}

export interface KeywordVolumeBatchFailure {
  batch_index: number;
  keyword_count: number;
  error: string;
}

export interface KeywordVolumeBatchReceipt {
  run_id: string;
  keyword_count: number;
  created_observations?: number;
  existing_observations?: number;
  from_cache?: boolean;
}

export interface KeywordVolumeRefreshResult {
  result_kind?: "keywords.volume_refresh";
  requested_phrases?: number;
  skipped_fresh?: number;
  fetched_phrases?: number;
  rejected_phrases?: KeywordVolumeRejectedPhrase[];
  batches?: KeywordVolumeBatchReceipt[];
  failed_batches?: KeywordVolumeBatchFailure[];
}

export interface KeywordVolumeRejectedPhrase {
  phrase: string;
  reason: string;
}

export interface KeywordResearchResult {
  result_kind?: "keywords.relationship_research";
  primary_keyword: string;
  site_id: string;
  research_doc_id: string;
  artifact: KeywordResearchArtifact;
  ingest: KeywordResearchIngestSummary;
  volume?: KeywordVolumeRefreshResult | null;
  classification?: KeywordClassifyResult | null;
}

// --- Completion Result Models ---

export interface LlmRequestResult {
  tokens_in?: number;
  tokens_out?: number;
  duration_ms?: number;
  finish_reason?: string;
  model?: string;
}

export interface ToolExecutionResult {
  success?: boolean;
  duration_ms?: number;
  error?: string | null;
}

export interface AggregatedUsageResult {
  by_model?: Record<string, ModelUsageSummary>;
  total?: UsageTotals;
}

export interface ModelUsageSummary {
  input_tokens?: number;
  output_tokens?: number;
  cached_input_tokens?: number;
  total_tokens?: number;
  api?: string;
  request_count?: number;
  cost?: number | null;
}

export interface TimingStatsResult {
  total_duration?: number | null;
  sum_duration?: number | null;
  api_duration?: number | null;
  tool_duration?: number | null;
  processing_duration?: number | null;
  iterations?: number | null;
  avg_iteration_duration?: number | null;
  first_token_seconds?: number | null;
}

export interface ToolCallByTool {
  count?: number;
  success?: number;
  error?: number;
}

export interface ToolCallStatsResult {
  total_tool_calls?: number;
  iterations_with_tools?: number;
  by_tool?: Record<string, ToolCallByTool>;
}

export interface UsageTotals {
  input_tokens?: number;
  output_tokens?: number;
  cached_input_tokens?: number;
  total_tokens?: number;
  total_requests?: number;
  unique_models?: number;
  total_cost?: number | null;
  known_cost_subtotal?: number;
  provider_reported_requests?: number;
  catalog_priced_requests?: number;
  unknown_cost_requests?: number;
}

export interface UserRequestResult {
  status?: string;
  output?: unknown;
  iterations?: number | null;
  total_usage?: AggregatedUsageResult | null;
  timing_stats?: TimingStatsResult | null;
  tool_call_stats?: ToolCallStatsResult | null;
  finish_reason?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface SubAgentResult {
  agent_name?: string;
  success?: boolean;
  error?: string | null;
}

export interface PersistenceResult {
  records_written?: number;
  duration_ms?: number;
}

// --- Typed Completion Event Interfaces (discriminated on `operation`) ---

// Each interface narrows CompletionPayload.result to its concrete type.
// Use TypedCompletionEvent instead of CompletionPayload when you need typed result.

export interface LlmRequestResult {
  tokens_in?: number;
  tokens_out?: number;
  duration_ms?: number;
  finish_reason?: string;
  model?: string;
}

export interface LlmRequestCompletionEvent {
  operation: "llm_request";
  operation_id: string;
  status: "cancelled" | "failed" | "success";
  result: LlmRequestResult;
}

export interface ToolExecutionResult {
  success?: boolean;
  duration_ms?: number;
  error?: string | null;
}

export interface ToolExecutionCompletionEvent {
  operation: "tool_execution";
  operation_id: string;
  status: "cancelled" | "failed" | "success";
  result: ToolExecutionResult;
}

export interface UserRequestResult {
  status?: string;
  output?: unknown;
  iterations?: number | null;
  total_usage?: AggregatedUsageResult | null;
  timing_stats?: TimingStatsResult | null;
  tool_call_stats?: ToolCallStatsResult | null;
  finish_reason?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface UserRequestCompletionEvent {
  operation: "user_request";
  operation_id: string;
  status: "cancelled" | "failed" | "success";
  result: UserRequestResult;
}

export interface SubAgentResult {
  agent_name?: string;
  success?: boolean;
  error?: string | null;
}

export interface SubAgentCompletionEvent {
  operation: "sub_agent";
  operation_id: string;
  status: "cancelled" | "failed" | "success";
  result: SubAgentResult;
}

export interface PersistenceResult {
  records_written?: number;
  duration_ms?: number;
}

export interface PersistenceCompletionEvent {
  operation: "persistence";
  operation_id: string;
  status: "cancelled" | "failed" | "success";
  result: PersistenceResult;
}

export type TypedCompletionEvent =
  | LlmRequestCompletionEvent
  | ToolExecutionCompletionEvent
  | UserRequestCompletionEvent
  | SubAgentCompletionEvent
  | PersistenceCompletionEvent;

const TYPED_COMPLETION_EVENT_OPERATIONS = new Set<Operation>([
  "llm_request", "tool_execution", "user_request", "sub_agent", "persistence",
]);

export function isTypedCompletionEvent(e: CompletionPayload): e is CompletionPayload & TypedCompletionEvent {
  return TYPED_COMPLETION_EVENT_OPERATIONS.has(e.operation as Operation) && e.result !== undefined;
}

// --- Tool Event Data Models ---

export interface ToolStartedData {
  arguments?: Record<string, unknown>;
  display_name?: string | null;
}

export interface ToolProgressData {
  percent?: number | null;
  metadata?: Record<string, unknown>;
}

export interface ToolStepData {
  step: string;
  metadata?: Record<string, unknown>;
}

export interface ToolResultPreviewData {
  preview: string;
}

export interface ToolCompletedData {
  result?: JsonValue;
  result_omitted?: boolean;
  result_preview?: string | null;
  output_chars?: number | null;
  tool_call_id?: string | null;
}

export interface ToolErrorData {
  error_type: string;
  detail?: string | null;
}

export interface ToolDelegatedData {
  arguments?: Record<string, unknown>;
}

export type TypedToolEventData =
  | ToolStartedData
  | ToolProgressData
  | ToolStepData
  | ToolResultPreviewData
  | ToolCompletedData
  | ToolErrorData
  | ToolDelegatedData;

// --- Typed Tool Event Interfaces (discriminated on `event`) ---

// Each interface narrows ToolEventPayload.data to its concrete type.
// Use TypedToolEvent instead of the base ToolEventPayload when you need typed data.

export interface ToolStartedData {
  arguments?: Record<string, unknown>;
  display_name?: string | null;
}

export interface ToolStartedToolEvent {
  event: "tool_started";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolStartedData;
}

export interface ToolProgressData {
  percent?: number | null;
  metadata?: Record<string, unknown>;
}

export interface ToolProgressToolEvent {
  event: "tool_progress";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolProgressData;
}

export interface ToolStepData {
  step: string;
  metadata?: Record<string, unknown>;
}

export interface ToolStepToolEvent {
  event: "tool_step";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolStepData;
}

export interface ToolResultPreviewData {
  preview: string;
}

export interface ToolResultPreviewToolEvent {
  event: "tool_result_preview";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolResultPreviewData;
}

export interface ToolCompletedData {
  result?: JsonValue;
  result_omitted?: boolean;
  result_preview?: string | null;
  output_chars?: number | null;
  tool_call_id?: string | null;
}

export interface ToolCompletedToolEvent {
  event: "tool_completed";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolCompletedData;
}

export interface ToolErrorData {
  error_type: string;
  detail?: string | null;
}

export interface ToolErrorToolEvent {
  event: "tool_error";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolErrorData;
}

export interface ToolDelegatedData {
  arguments?: Record<string, unknown>;
}

export interface ToolDelegatedToolEvent {
  event: "tool_delegated";
  call_id: string;
  tool_name: string;
  timestamp?: number;
  message?: string | null;
  show_spinner?: boolean;
  data: ToolDelegatedData;
}

export type TypedToolEvent =
  | ToolStartedToolEvent
  | ToolProgressToolEvent
  | ToolStepToolEvent
  | ToolResultPreviewToolEvent
  | ToolCompletedToolEvent
  | ToolErrorToolEvent
  | ToolDelegatedToolEvent;

const TYPED_TOOL_EVENT_TYPES = new Set<ToolEventType>([
  "tool_started", "tool_progress", "tool_step", "tool_result_preview", "tool_completed", "tool_error", "tool_delegated",
]);

export function isTypedToolEvent(e: ToolEventPayload): e is ToolEventPayload & TypedToolEvent {
  return TYPED_TOOL_EVENT_TYPES.has(e.event as ToolEventType) && e.data !== undefined;
}

// --- Render Block Data Models (RenderBlockPayload.data per type) ---

export interface TextBlockData {
}

export interface CodeBlockData {
  language?: string;
  code?: string;
  is_diff?: boolean;
}

export interface DiffBlockData {
  language?: string;
  style?: string;
  code?: string;
}

export interface ThinkingBlockData {
}

export interface ReasoningBlockData {
}

export interface ConsolidatedReasoningBlockData {
  reasoning_texts: string[];
}

export interface ImageBlockData {
  src: string;
  alt?: string;
}

export interface VideoBlockData {
  src: string;
  alt?: string;
}

export interface MermaidBlockData {
  title?: string | null;
  diagramType?: string;
  source: string;
  isValid?: boolean | null;
  diagnostics?: string[];
}

export interface TableBlockData {
  headers: string[];
  rows: string[][];
  isComplete?: boolean;
  rawMarkdown?: string;
}

// --- Typed Render Block Interfaces (discriminated on `type`) ---

// Each interface narrows RenderBlockPayload.data to its concrete type.
// Use TypedRenderBlock instead of RenderBlockPayload when you need typed data.

export interface TextRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "text";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: TextBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface CodeRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "code";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: CodeBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface TableRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "table";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: TableBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface ThinkingRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "thinking";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: ThinkingBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface ReasoningRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "reasoning";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: ReasoningBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface ConsolidatedReasoningRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "consolidated_reasoning";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: ConsolidatedReasoningBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface ImageRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "image";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: ImageBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface VideoRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "video";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: VideoBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface DecisionRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "decision";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
}

export interface MermaidRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "mermaid";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: MermaidBlockData | null;
  metadata?: Record<string, unknown>;
}

export interface ArtifactRenderBlock {
  blockId: string;
  blockIndex: number;
  type: "artifact";
  status: "streaming" | "complete" | "error";
  content?: string | null;
  data?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
}

export type TypedRenderBlock =
  | TextRenderBlock
  | CodeRenderBlock
  | TableRenderBlock
  | ThinkingRenderBlock
  | ReasoningRenderBlock
  | ConsolidatedReasoningRenderBlock
  | ImageRenderBlock
  | VideoRenderBlock
  | DecisionRenderBlock
  | MermaidRenderBlock
  | ArtifactRenderBlock;

const TYPED_RENDER_BLOCK_TYPES = new Set<string>([
  "text", "code", "table", "thinking", "reasoning", "consolidated_reasoning", "image", "video", "decision", "mermaid", "artifact",
]);

export function isTypedRenderBlock(e: RenderBlockPayload): e is RenderBlockPayload & TypedRenderBlock {
  return TYPED_RENDER_BLOCK_TYPES.has(e.type);
}

// --- Server Data-Event Render Blocks (FE-synthesized wrappers) ---

// NOT render_block wire events — the frontend synthesizes these block wrappers
// when it converts a `data` stream event (or FE parse state) into a renderable
// block. Generated from matrx_connect.context.data_render_blocks; classified per
// the content-vocab crosswalk. Protocol/lifecycle events live in their own union
// (ServerProtocolRenderBlock) and are never mixed into Shape blocks.

export interface SearchReplaceRenderData {
  search: string;
  replace: string;
  searchComplete: boolean;
  replaceComplete: boolean;
  isComplete: boolean;
  language?: string | null;
}

export interface DirectiveReceiptRenderData {
  directive: string;
  outcome: "already_applied" | "applied" | "blocked" | "failed" | "pending" | "proposed";
  message: string;
  resource_kind?: string;
  resource_ids?: string[];
}

export interface UnknownDataEventData {
  [key: string]: unknown;
  _dataType: string;
}

/** Audio output from the AI (TTS or file). */
export interface AudioOutputRenderBlock {
  type: "audio_output";
  content: string;
  data: AudioOutputData;
  metadata?: Record<string, unknown>;
}

/** Image output from the AI. Display priority: cdn_url → file handler (via file_id) → url (durable). */
export interface ImageOutputRenderBlock {
  type: "image_output";
  content: string;
  data: ImageOutputData;
  metadata?: Record<string, unknown>;
}

/** Video output from the AI. */
export interface VideoOutputRenderBlock {
  type: "video_output";
  content: string;
  data: VideoOutputData;
  metadata?: Record<string, unknown>;
}

/** Web search results block — registered kind `search_results` (inactive; data-event arrival unchanged). */
export interface SearchResultsRenderBlock {
  type: "search_results";
  content: string;
  data: SearchResultsData;
  metadata?: Record<string, unknown>;
}

/** URL fetch results block — registered kind `fetch_results` (inactive; data-event arrival unchanged). */
export interface FetchResultsRenderBlock {
  type: "fetch_results";
  content: string;
  data: FetchResultsData;
  metadata?: Record<string, unknown>;
}

/** Prompt categorization result — registered kind `categorization_result` (inactive; data-event arrival unchanged). */
export interface CategorizationResultRenderBlock {
  type: "categorization_result";
  content: string;
  data: CategorizationResultData;
  metadata?: Record<string, unknown>;
}

/** A decision turn's answers — registered kind `decision_answers`. The LIVE arrival of the same part the assistant message persists, so a runner or a battle column shows the decision as it lands instead of only after a reload. `content` is null: the frontend commits the payload as a real `decision_answers` message part (the same part the server persists), never as reconstructed markdown. */
export interface DecisionAnswersRenderBlock {
  type: "decision_answers";
  /** Always null — a non-null content would leak into committed message parts. The payload lives on `data`. */
  content: null;
  data: DecisionAnswersData;
  metadata?: Record<string, unknown>;
}

/** Questionnaire to display — alias of the registered `questionnaire` kind. */
export interface DisplayQuestionnaireRenderBlock {
  type: "display_questionnaire";
  content: string;
  data: QuestionnaireDisplayData;
  metadata?: Record<string, unknown>;
}

/** Generic tool-result envelope — payload typing is owned by tool_io contracts. */
export interface FunctionResultRenderBlock {
  type: "function_result";
  content: string;
  data: FunctionResultData;
  metadata?: Record<string, unknown>;
}

/** Workflow progress event. */
export interface WorkflowStepRenderBlock {
  type: "workflow_step";
  content: string;
  data: WorkflowStepData;
  metadata?: Record<string, unknown>;
}

/** A mandate run PARKED for a person's answer — the frontend renders the server's sentence plus the served inputs as a form whose Continue posts `POST /mandates/runs/{request_id}/answer` and streams the resumed run. Until 2026-10-17 it fell to the Unknown Data Event card. */
export interface MandateInputRequiredRenderBlock {
  type: "mandate_input_required";
  content: string;
  data: MandateInputRequiredData;
  metadata?: Record<string, unknown>;
}

/** Web search failure event. */
export interface SearchErrorRenderBlock {
  type: "search_error";
  content: string;
  data: SearchErrorData;
  metadata?: Record<string, unknown>;
}

/** Warning about malformed structured input blocks. */
export interface StructuredInputWarningRenderBlock {
  type: "structured_input_warning";
  content: string;
  data: StructuredInputWarningData;
  metadata?: Record<string, unknown>;
}

/** Podcast pipeline lifecycle event (stage finished). */
export interface PodcastStageRenderBlock {
  type: "podcast_stage";
  content: string;
  data: PodcastStageEvent;
  metadata?: Record<string, unknown>;
}

/** Podcast pipeline lifecycle event (generation complete). */
export interface PodcastCompleteRenderBlock {
  type: "podcast_complete";
  content: string;
  data: PodcastCompleteEvent;
  metadata?: Record<string, unknown>;
}

/** Scrape pipeline lifecycle event. */
export interface ScrapeBatchCompleteRenderBlock {
  type: "scrape_batch_complete";
  content: string;
  data: ScrapeBatchCompleteData;
  metadata?: Record<string, unknown>;
}

/** Conversation Value Store 'result ready' card — FE-synthesized from the kind-discriminated value_store.stored data event. Never persisted to cx_message.content. */
export interface ValueStoreStoredRenderBlock {
  type: "value_store_stored";
  /** Always null — a non-null content would leak into committed message parts. The payload lives on `data`. */
  content: null;
  data: ValueStoredEvent;
  metadata?: Record<string, unknown>;
}

/** Context-groom receipt line — FE-synthesized from the value_store.groomed data event. Subtle indicator only; never persisted. */
export interface ContextGroomedRenderBlock {
  type: "context_groomed";
  /** Always null — a non-null content would leak into committed message parts. The payload lives on `data`. */
  content: null;
  data: ContextGroomedEvent;
  metadata?: Record<string, unknown>;
}

/** Search-and-replace block for code editing (FE parse state). */
export interface SearchReplaceRenderBlock {
  type: "search_replace";
  content: string;
  data?: SearchReplaceRenderData;
  metadata?: Record<string, unknown>;
}

/** Kind Directive apply receipt — FE-synthesized from the kind-discriminated directive_apply.* events. Carries the SERVER's own sentence for the outcome (created / already applied / proposed / failed / blocked); never composed client-side. Never persisted to cx_message.content. */
export interface DirectiveReceiptRenderBlock {
  type: "directive_receipt";
  /** Always null — a non-null content would leak into committed message parts. The payload lives on `data`. */
  content: null;
  data: DirectiveReceiptRenderData;
  metadata?: Record<string, unknown>;
}

/** Fallback for data events whose type is not recognized; _dataType preserves the original type string. */
export interface UnknownDataEventRenderBlock {
  type: "unknown_data_event";
  content: string;
  data: UnknownDataEventData;
  metadata?: Record<string, unknown>;
}

/** Protocol/lifecycle/ack events — control plumbing, never Shapes. */
export type ServerProtocolRenderBlock =
  | FunctionResultRenderBlock
  | WorkflowStepRenderBlock
  | MandateInputRequiredRenderBlock
  | SearchErrorRenderBlock
  | StructuredInputWarningRenderBlock
  | PodcastStageRenderBlock
  | PodcastCompleteRenderBlock
  | ScrapeBatchCompleteRenderBlock
  | ValueStoreStoredRenderBlock
  | ContextGroomedRenderBlock
  | SearchReplaceRenderBlock
  | DirectiveReceiptRenderBlock;

export const SERVER_PROTOCOL_RENDER_BLOCK_TYPES = new Set<string>([
  "function_result", "workflow_step", "mandate_input_required", "search_error", "structured_input_warning", "podcast_stage", "podcast_complete", "scrape_batch_complete", "value_store_stored", "context_groomed", "search_replace", "directive_receipt",
]);

/** Generated-media delivery blocks — generic media primitives. */
export type ServerScalarGenericRenderBlock =
  | AudioOutputRenderBlock
  | ImageOutputRenderBlock
  | VideoOutputRenderBlock;

export const SERVER_SCALAR_GENERIC_RENDER_BLOCK_TYPES = new Set<string>([
  "audio_output", "image_output", "video_output",
]);

/** Typed server result displays — registered-kind aliases or shape candidates. */
export type ServerShapeRenderBlock =
  | SearchResultsRenderBlock
  | FetchResultsRenderBlock
  | CategorizationResultRenderBlock
  | DecisionAnswersRenderBlock
  | DisplayQuestionnaireRenderBlock;

export const SERVER_SHAPE_RENDER_BLOCK_TYPES = new Set<string>([
  "search_results", "fetch_results", "categorization_result", "decision_answers", "display_questionnaire",
]);

/** Deliberately untyped catch-alls. */
export type ServerOpaqueRenderBlock =
  | UnknownDataEventRenderBlock;

export const SERVER_INTENTIONALLY_OPAQUE_RENDER_BLOCK_TYPES = new Set<string>([
  "unknown_data_event",
]);

/** Every FE-synthesized data-event render block — the generated successor of
 * the hand-maintained ServerOnlyRenderBlock union in missing-types.ts. */
export type ServerOnlyRenderBlock =
  | ServerProtocolRenderBlock
  | ServerScalarGenericRenderBlock
  | ServerShapeRenderBlock
  | ServerOpaqueRenderBlock;

export type ServerOnlyBlockType = ServerOnlyRenderBlock["type"];

// --- Message Part Models (cx_message.content[] items) ---

export interface NormalizedCitation {
  kind: "document_block" | "document_char" | "document_page" | "grounding" | "search_result" | "web";
  provider: "anthropic" | "google" | "openai" | "xai";
  cited_text?: string | null;
  title?: string | null;
  url?: string | null;
  source_index?: number;
  file_id?: string | null;
  page?: number | null;
  end_page?: number | null;
  source_start?: number | null;
  source_end?: number | null;
  answer_start?: number | null;
  answer_end?: number | null;
  raw?: Record<string, unknown>;
}

export interface TextPart {
  metadata?: Record<string, JsonValue>;
  type: "text";
  text?: string;
  id?: string;
  citations?: NormalizedCitation[];
}

export interface ThinkingPart {
  metadata?: Record<string, JsonValue>;
  type: "thinking";
  text?: string;
  id?: string;
  provider?: "anthropic" | "cerebras" | "generic_openai" | "google" | "groq" | "moonshot" | "openai" | "together" | "xai" | null;
  signature?: string | null;
  signature_encoding?: "base64" | null;
  summary?: unknown[];
}

export interface ToolCallPart {
  metadata?: Record<string, JsonValue>;
  type: "tool_call";
  call_id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
}

export interface ToolResultPart {
  metadata?: Record<string, JsonValue>;
  type: "tool_result";
  call_id?: string;
  tool_use_id?: string;
  name?: string;
  is_error?: boolean;
  output_chars?: number;
  output_preview?: Record<string, unknown> | null;
}

export type ImageMediaPart = {
  metadata?: Record<string, JsonValue>;
  origin?: "external" | "matrx" | null;
  file_id?: string | null;
  url?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  visibility?: string | null;
  cdn_url?: string | null;
  type: "media";
  kind: "image";
  width?: number | null;
  height?: number | null;
  role?: "asset" | "character" | "composition_control" | "edit_target" | "first_frame" | "last_frame" | "mask" | "style" | "subject" | null;
  name?: string | null;
} & ({
  url: string;
} | {
  file_id: string;
});

export type AudioMediaPart = {
  metadata?: Record<string, JsonValue>;
  origin?: "external" | "matrx" | null;
  file_id?: string | null;
  url?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  visibility?: string | null;
  cdn_url?: string | null;
  type: "media";
  kind: "audio";
  duration_ms?: number | null;
  transcription_result?: string | null;
  role?: "lip_sync" | null;
} & ({
  url: string;
} | {
  file_id: string;
});

export type VideoMediaPart = {
  metadata?: Record<string, JsonValue>;
  origin?: "external" | "matrx" | null;
  file_id?: string | null;
  url?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  visibility?: string | null;
  cdn_url?: string | null;
  type: "media";
  kind: "video";
  width?: number | null;
  height?: number | null;
  duration_ms?: number | null;
  role?: "extend" | "restyle" | null;
  name?: string | null;
} & ({
  url: string;
} | {
  file_id: string;
});

export type DocumentMediaPart = {
  metadata?: Record<string, JsonValue>;
  origin?: "external" | "matrx" | null;
  file_id?: string | null;
  url?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  visibility?: string | null;
  cdn_url?: string | null;
  type: "media";
  kind: "document";
  width?: number | null;
  height?: number | null;
  page_count?: number | null;
} & ({
  url: string;
} | {
  file_id: string;
});

export interface YouTubeMediaPart {
  metadata?: Record<string, JsonValue>;
  type: "media";
  kind: "youtube";
  url: string;
  external_url?: string | null;
  origin?: "external";
  file_id?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
}

export interface CodeExecPart {
  metadata?: Record<string, JsonValue>;
  type: "code_exec";
  language?: string;
  code?: string;
}

export interface CodeResultPart {
  metadata?: Record<string, JsonValue>;
  type: "code_result";
  output?: string;
  outcome?: string;
}

export interface WebSearchPart {
  metadata?: Record<string, JsonValue>;
  type: "web_search";
  id?: string;
  status?: string;
}

export interface HostedToolPart {
  metadata?: Record<string, JsonValue>;
  type: "hosted_tool";
  provider?: string;
  block?: Record<string, unknown>;
}

export interface PreFetchedUrl {
  [key: string]: unknown;
  url: string;
  textContent: string;
  title?: string | null;
  scrapedAt?: string | null;
  charCount?: number | null;
}

export interface WebpageInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_webpage";
  urls: (string | PreFetchedUrl)[];
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface LiveResourceRefInput {
  [key: string]: unknown;
  id: string;
  mode?: "reference";
}

export type ResourceRefInput = LiveResourceRefInput | SnapshotContentResourceRefInput | SnapshotTextResourceRefInput | SnapshotBodyResourceRefInput | SnapshotDescriptionResourceRefInput | SnapshotValueResourceRefInput;

export interface SnapshotBodyResourceRefInput {
  [key: string]: unknown;
  mode: "snapshot";
  body: string;
}

export interface SnapshotContentResourceRefInput {
  [key: string]: unknown;
  mode: "snapshot";
  content: string;
}

export interface SnapshotDescriptionResourceRefInput {
  [key: string]: unknown;
  mode: "snapshot";
  description: string;
}

export interface SnapshotTextResourceRefInput {
  [key: string]: unknown;
  mode: "snapshot";
  text: string;
}

export interface SnapshotValueResourceRefInput {
  [key: string]: unknown;
  mode: "snapshot";
  value: string;
}

export interface NotesInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_notes";
  note_ids: (string | ResourceRefInput)[];
  template?: string;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface TaskInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_task";
  task_ids: (string | ResourceRefInput)[];
  template?: string;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface AgentInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_agent";
  agent_ids: string[];
}

export interface ProjectInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_project";
  project_ids: string[];
}

export interface AgentAppInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_agent_app";
  agent_app_ids: string[];
}

export interface TranscriptInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_transcript";
  transcript_ids: string[];
}

export interface TranscriptSessionInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_transcript_session";
  transcript_session_ids: string[];
}

export interface WorkbookInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_workbook";
  workbook_ids: (string | LiveResourceRefInput)[];
}

export interface DocumentInputPart {
  metadata?: Record<string, JsonValue>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  type: "input_document";
  document_ids: (string | LiveResourceRefInput)[];
}

export interface FullTableBookmark {
  [key: string]: unknown;
  type: "full_table";
  table_id: string;
  table_name?: string | null;
}

export interface TableCellBookmark {
  [key: string]: unknown;
  type: "table_cell";
  table_id: string;
  row_id: string;
  column_name: string;
  table_name?: string | null;
}

export interface TableColumnBookmark {
  [key: string]: unknown;
  type: "table_column";
  table_id: string;
  column_name: string;
  table_name?: string | null;
}

export interface TableRowBookmark {
  [key: string]: unknown;
  type: "table_row";
  table_id: string;
  row_id: string;
  table_name?: string | null;
}

export interface TableSchemaBookmark {
  [key: string]: unknown;
  type: "table_schema";
  table_id: string;
  table_name?: string | null;
}

export interface TableInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_table";
  bookmarks: (FullTableBookmark | TableColumnBookmark | TableRowBookmark | TableCellBookmark | TableSchemaBookmark)[];
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface FullListBookmark {
  [key: string]: unknown;
  type: "full_list";
  list_id: string;
  list_name?: string | null;
}

export interface ListGroupBookmark {
  [key: string]: unknown;
  type: "list_group";
  list_id: string;
  group_name: string;
  list_name?: string | null;
}

export interface ListItemBookmark {
  [key: string]: unknown;
  type: "list_item";
  list_id: string;
  item_id: string;
  list_name?: string | null;
}

export interface ListInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_list";
  bookmarks: (FullListBookmark | ListGroupBookmark | ListItemBookmark)[];
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface DataRefSort {
  field: string;
  direction?: "asc" | "desc";
}

export interface DbFieldRef {
  table: "notes" | "organizations" | "projects" | "tasks";
  label?: string;
  optional_context?: boolean;
  ref_type: "db_field";
  id: string;
  field_name: string;
}

export interface DbQueryRef {
  table: "notes" | "organizations" | "projects" | "tasks";
  label?: string;
  optional_context?: boolean;
  ref_type: "db_query";
  filter?: Record<string, unknown>;
  fields?: string[] | null;
  sort?: DataRefSort | null;
  limit?: number;
}

export interface DbRecordRef {
  table: "notes" | "organizations" | "projects" | "tasks";
  label?: string;
  optional_context?: boolean;
  ref_type: "db_record";
  id: string;
  fields?: string[] | null;
}

export interface DataInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_data";
  refs: (DbRecordRef | DbQueryRef | DbFieldRef)[];
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface ContextInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_context";
  context_id?: string;
  context_name?: string;
  context_data?: Record<string, unknown>;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface BlockStateRef {
  id: string;
  state_version: number;
}

export interface RemarkAnswer {
  question: string;
  answer: string | boolean | number | string[];
  name?: string | null;
  type: "choice" | "noul" | "score" | "text" | null;
}

export interface RemarkItem {
  kind: "answers" | "choice" | "comment" | "edit" | "interaction";
  target?: RemarkTarget | null;
  quote?: string | null;
  body?: string | null;
  diff?: string | null;
  answers?: RemarkAnswer[] | null;
  title?: string | null;
  comment_id?: string | null;
  id?: string | null;
  handle?: string | null;
  thread?: RemarkThreadEntry[] | null;
  block_state_ref?: BlockStateRef | null;
  metadata?: Record<string, unknown>;
}

export interface RemarkTarget {
  message_id?: string | null;
  record_token?: string | null;
  record_id?: string | null;
  record_title?: string | null;
}

export interface RemarkThreadEntry {
  author_name: string;
  author_kind: "agent" | "person";
  body: string;
  created_at?: string | null;
}

export interface RemarksInputPart {
  metadata?: Record<string, JsonValue>;
  type: "input_remarks";
  items: RemarkItem[];
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface CellPart {
  scope: "cell";
  row_id: string;
  column: string;
}

export interface ColumnPart {
  scope: "column";
  column: string;
}

export interface ContextCellPart {
  scope: "context_cell";
  scope_id: string;
}

export interface GroupPart {
  scope: "group";
  group: string;
}

export interface ItemPart {
  scope: "item";
  item_id: string;
}

export interface PagePart {
  scope: "page";
  page: number;
}

export interface ResourcePromotion {
  representation: string;
  max_chars?: number;
}

export interface RowPart {
  scope: "row";
  row_id: string;
}

export interface SchemaPart {
  scope: "schema";
}

export interface SheetPart {
  scope: "sheet";
  sheet: string;
}

export interface SourceRef {
  envelope?: "resource_ref";
  resource_type: string;
  resource_id: string;
  representation?: string | null;
  include_segments?: string[] | null;
  delivery?: "context" | "direct" | null;
  max_chars?: number | null;
  promote?: ResourcePromotion | ResourcePromotion[] | null;
  exclude?: string[] | null;
  label?: string | null;
  overlay?: Record<string, JsonValue> | null;
  part?: ColumnPart | RowPart | CellPart | SchemaPart | GroupPart | ItemPart | PagePart | SheetPart | ContextCellPart | null;
  editable?: boolean | null;
  keep_fresh?: boolean | null;
  optional_context?: boolean | null;
  template?: "compact" | "full" | "minimal" | null;
  persistence?: "durable" | "turn" | null;
}

export interface SourceSet {
  envelope?: "source_set";
  version?: 1;
  sources?: SourceRef[];
  topic?: string | null;
  grounding?: "retrieve" | "selected" | "whole";
  retrieve_query?: string | null;
  target_model_id?: string | null;
}

export interface SourceSetPart {
  metadata?: Record<string, JsonValue>;
  type: "source_set";
  source_set: SourceSet;
  convert_to_text?: boolean;
  optional_context?: boolean;
  keep_fresh?: boolean;
  editable?: boolean | null;
}

export interface DecisionQuestion {
  __kind?: "";
  name: string;
  type: "box" | "choice" | "noul" | "score";
  instructions: string;
  criteria?: Record<string, string> | string[] | null;
  image?: number | null;
  guidance?: string | null;
  suggested_threshold?: number | null;
}

export interface DecisionQuestionsPart {
  metadata?: Record<string, JsonValue>;
  type: "decision_questions";
  __kind: "decision_questions";
  questions: DecisionQuestion[];
}

export interface DecisionAnswer {
  __kind?: "decision_answer";
  type: "box" | "choice" | "noul" | "score";
  answer: DecisionBox | boolean | number | string;
  probability?: number | null;
  probabilities?: Record<string, number> | null;
  confidence?: number | null;
  image?: number | null;
  legend?: Record<string, string> | null;
}

export interface DecisionBox {
  __kind?: "";
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface DecisionUsage {
  __kind?: "";
  input_tokens: number;
  output_tokens: number;
}

export interface DecisionAnswersPart {
  metadata?: Record<string, JsonValue>;
  type: "decision_answers";
  __kind: "decision_answers";
  model: string;
  method: "native" | "verbalized" | "verbalized_calibrated";
  answers?: Record<string, DecisionAnswer>;
  unanswerable?: Record<string, string>;
  usage: DecisionUsage;
  cost_usd: number;
}

export interface SpeechTurn {
  __kind?: "";
  speaker: string;
  voice?: string | null;
  text: string;
  direction?: string | null;
  pause_after_ms?: number | null;
}

export interface SpeechScriptPart {
  metadata?: Record<string, JsonValue>;
  type: "speech_script";
  __kind: "speech_script";
  turns: SpeechTurn[];
}

export type MessagePart =
  | TextPart
  | ThinkingPart
  | ToolCallPart
  | ToolResultPart
  | ImageMediaPart
  | AudioMediaPart
  | VideoMediaPart
  | DocumentMediaPart
  | YouTubeMediaPart
  | CodeExecPart
  | CodeResultPart
  | WebSearchPart
  | HostedToolPart
  | WebpageInputPart
  | NotesInputPart
  | TaskInputPart
  | AgentInputPart
  | ProjectInputPart
  | AgentAppInputPart
  | TranscriptInputPart
  | TranscriptSessionInputPart
  | WorkbookInputPart
  | DocumentInputPart
  | TableInputPart
  | ListInputPart
  | DataInputPart
  | ContextInputPart
  | RemarksInputPart
  | SourceSetPart
  | DecisionQuestionsPart
  | DecisionAnswersPart
  | SpeechScriptPart;

interface MessagePartJsonSchema {
  [key: string]: unknown;
  $ref?: string;
  anyOf?: MessagePartJsonSchema[];
  oneOf?: MessagePartJsonSchema[];
  allOf?: MessagePartJsonSchema[];
  type?: "object" | "array" | "string" | "number" | "integer" | "boolean" | "null";
  const?: unknown;
  enum?: unknown[];
  required?: string[];
  properties?: Record<string, MessagePartJsonSchema>;
  items?: MessagePartJsonSchema;
  minItems?: number;
  minLength?: number;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  additionalProperties?: boolean | MessagePartJsonSchema;
  $defs?: Record<string, MessagePartJsonSchema>;
}

const MESSAGE_PART_SCHEMA: MessagePartJsonSchema = {
  "$defs": {
    "AgentAppInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_agent_app",
          "type": "string"
        },
        "agent_app_ids": {
          "items": {
            "minLength": 1,
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "agent_app_ids",
        "type"
      ],
      "type": "object"
    },
    "AgentInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_agent",
          "type": "string"
        },
        "agent_ids": {
          "items": {
            "minLength": 1,
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "agent_ids",
        "type"
      ],
      "type": "object"
    },
    "AudioMediaPart": {
      "additionalProperties": false,
      "allOf": [
        {
          "anyOf": [
            {
              "properties": {
                "url": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "url"
              ]
            },
            {
              "properties": {
                "file_id": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "file_id"
              ]
            }
          ]
        }
      ],
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "origin": {
          "anyOf": [
            {
              "enum": [
                "matrx",
                "external"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "file_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "mime_type": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "size_bytes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "visibility": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "cdn_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "media",
          "type": "string"
        },
        "kind": {
          "const": "audio",
          "type": "string"
        },
        "duration_ms": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "transcription_result": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "role": {
          "anyOf": [
            {
              "const": "lip_sync",
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type",
        "kind"
      ],
      "type": "object"
    },
    "BlockStateRef": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "maxLength": 36,
          "minLength": 36,
          "type": "string"
        },
        "state_version": {
          "minimum": 1,
          "type": "integer"
        }
      },
      "required": [
        "id",
        "state_version"
      ],
      "type": "object"
    },
    "CellPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "cell",
          "type": "string"
        },
        "row_id": {
          "minLength": 1,
          "type": "string"
        },
        "column": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "row_id",
        "column"
      ],
      "type": "object"
    },
    "CodeExecPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "code_exec",
          "type": "string"
        },
        "language": {
          "type": "string"
        },
        "code": {
          "type": "string"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "CodeResultPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "code_result",
          "type": "string"
        },
        "output": {
          "type": "string"
        },
        "outcome": {
          "type": "string"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "ColumnPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "column",
          "type": "string"
        },
        "column": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "column"
      ],
      "type": "object"
    },
    "ContextCellPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "context_cell",
          "type": "string"
        },
        "scope_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "scope_id"
      ],
      "type": "object"
    },
    "ContextInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_context",
          "type": "string"
        },
        "context_id": {
          "type": "string"
        },
        "context_name": {
          "type": "string"
        },
        "context_data": {
          "additionalProperties": true,
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "DataInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_data",
          "type": "string"
        },
        "refs": {
          "items": {
            "discriminator": {
              "mapping": {
                "db_field": "#/$defs/DbFieldRef",
                "db_query": "#/$defs/DbQueryRef",
                "db_record": "#/$defs/DbRecordRef"
              },
              "propertyName": "ref_type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/DbRecordRef"
              },
              {
                "$ref": "#/$defs/DbQueryRef"
              },
              {
                "$ref": "#/$defs/DbFieldRef"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "refs",
        "type"
      ],
      "type": "object"
    },
    "DataRefSort": {
      "additionalProperties": false,
      "properties": {
        "field": {
          "minLength": 1,
          "type": "string"
        },
        "direction": {
          "enum": [
            "asc",
            "desc"
          ],
          "type": "string"
        }
      },
      "required": [
        "field"
      ],
      "type": "object"
    },
    "DbFieldRef": {
      "additionalProperties": false,
      "properties": {
        "table": {
          "enum": [
            "notes",
            "tasks",
            "projects",
            "organizations"
          ],
          "type": "string"
        },
        "label": {
          "type": "string"
        },
        "optional_context": {
          "type": "boolean"
        },
        "ref_type": {
          "const": "db_field",
          "type": "string"
        },
        "id": {
          "minLength": 1,
          "type": "string"
        },
        "field_name": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "table",
        "ref_type",
        "id",
        "field_name"
      ],
      "type": "object"
    },
    "DbQueryRef": {
      "additionalProperties": false,
      "properties": {
        "table": {
          "enum": [
            "notes",
            "tasks",
            "projects",
            "organizations"
          ],
          "type": "string"
        },
        "label": {
          "type": "string"
        },
        "optional_context": {
          "type": "boolean"
        },
        "ref_type": {
          "const": "db_query",
          "type": "string"
        },
        "filter": {
          "additionalProperties": true,
          "type": "object"
        },
        "fields": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "sort": {
          "anyOf": [
            {
              "$ref": "#/$defs/DataRefSort"
            },
            {
              "type": "null"
            }
          ]
        },
        "limit": {
          "maximum": 1000,
          "minimum": 1,
          "type": "integer"
        }
      },
      "required": [
        "table",
        "ref_type"
      ],
      "type": "object"
    },
    "DbRecordRef": {
      "additionalProperties": false,
      "properties": {
        "table": {
          "enum": [
            "notes",
            "tasks",
            "projects",
            "organizations"
          ],
          "type": "string"
        },
        "label": {
          "type": "string"
        },
        "optional_context": {
          "type": "boolean"
        },
        "ref_type": {
          "const": "db_record",
          "type": "string"
        },
        "id": {
          "minLength": 1,
          "type": "string"
        },
        "fields": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "table",
        "ref_type",
        "id"
      ],
      "type": "object"
    },
    "DecisionAnswer": {
      "additionalProperties": false,
      "properties": {
        "__kind": {
          "const": "decision_answer",
          "type": "string"
        },
        "type": {
          "enum": [
            "noul",
            "choice",
            "score",
            "box"
          ],
          "type": "string"
        },
        "answer": {
          "anyOf": [
            {
              "$ref": "#/$defs/DecisionBox"
            },
            {
              "type": "boolean"
            },
            {
              "type": "number"
            },
            {
              "type": "string"
            }
          ]
        },
        "probability": {
          "anyOf": [
            {
              "maximum": 1.0,
              "minimum": 0.0,
              "type": "number"
            },
            {
              "type": "null"
            }
          ]
        },
        "probabilities": {
          "anyOf": [
            {
              "additionalProperties": {
                "type": "number"
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ]
        },
        "confidence": {
          "anyOf": [
            {
              "maximum": 1.0,
              "minimum": 0.0,
              "type": "number"
            },
            {
              "type": "null"
            }
          ]
        },
        "image": {
          "anyOf": [
            {
              "minimum": 0,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "legend": {
          "anyOf": [
            {
              "additionalProperties": {
                "type": "string"
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type",
        "answer"
      ],
      "type": "object"
    },
    "DecisionAnswersPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "decision_answers",
          "type": "string"
        },
        "__kind": {
          "const": "decision_answers",
          "type": "string"
        },
        "model": {
          "minLength": 1,
          "type": "string"
        },
        "method": {
          "enum": [
            "native",
            "verbalized",
            "verbalized_calibrated"
          ],
          "type": "string"
        },
        "answers": {
          "additionalProperties": {
            "$ref": "#/$defs/DecisionAnswer"
          },
          "type": "object"
        },
        "unanswerable": {
          "additionalProperties": {
            "type": "string"
          },
          "type": "object"
        },
        "usage": {
          "$ref": "#/$defs/DecisionUsage"
        },
        "cost_usd": {
          "minimum": 0.0,
          "type": "number"
        }
      },
      "required": [
        "model",
        "method",
        "usage",
        "cost_usd",
        "type"
      ],
      "type": "object"
    },
    "DecisionBox": {
      "additionalProperties": false,
      "properties": {
        "__kind": {
          "const": "",
          "type": "string"
        },
        "top": {
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        "bottom": {
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        "left": {
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        },
        "right": {
          "maximum": 1.0,
          "minimum": 0.0,
          "type": "number"
        }
      },
      "required": [
        "top",
        "bottom",
        "left",
        "right"
      ],
      "type": "object"
    },
    "DecisionQuestion": {
      "additionalProperties": false,
      "properties": {
        "__kind": {
          "const": "",
          "type": "string"
        },
        "name": {
          "maxLength": 128,
          "minLength": 1,
          "type": "string"
        },
        "type": {
          "enum": [
            "noul",
            "choice",
            "score",
            "box"
          ],
          "type": "string"
        },
        "instructions": {
          "minLength": 1,
          "type": "string"
        },
        "criteria": {
          "anyOf": [
            {
              "additionalProperties": {
                "type": "string"
              },
              "type": "object"
            },
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "image": {
          "anyOf": [
            {
              "minimum": 0,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "guidance": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "suggested_threshold": {
          "anyOf": [
            {
              "maximum": 1.0,
              "minimum": 0.0,
              "type": "number"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "name",
        "type",
        "instructions"
      ],
      "type": "object"
    },
    "DecisionQuestionsPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "decision_questions",
          "type": "string"
        },
        "__kind": {
          "const": "decision_questions",
          "type": "string"
        },
        "questions": {
          "items": {
            "$ref": "#/$defs/DecisionQuestion"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "questions",
        "type"
      ],
      "type": "object"
    },
    "DecisionUsage": {
      "additionalProperties": false,
      "properties": {
        "__kind": {
          "const": "",
          "type": "string"
        },
        "input_tokens": {
          "minimum": 0,
          "type": "integer"
        },
        "output_tokens": {
          "minimum": 0,
          "type": "integer"
        }
      },
      "required": [
        "input_tokens",
        "output_tokens"
      ],
      "type": "object"
    },
    "DocumentInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_document",
          "type": "string"
        },
        "document_ids": {
          "items": {
            "anyOf": [
              {
                "minLength": 1,
                "type": "string"
              },
              {
                "$ref": "#/$defs/LiveResourceRefInput"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "document_ids",
        "type"
      ],
      "type": "object"
    },
    "DocumentMediaPart": {
      "additionalProperties": false,
      "allOf": [
        {
          "anyOf": [
            {
              "properties": {
                "url": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "url"
              ]
            },
            {
              "properties": {
                "file_id": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "file_id"
              ]
            }
          ]
        }
      ],
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "origin": {
          "anyOf": [
            {
              "enum": [
                "matrx",
                "external"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "file_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "mime_type": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "size_bytes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "visibility": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "cdn_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "media",
          "type": "string"
        },
        "kind": {
          "const": "document",
          "type": "string"
        },
        "width": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "height": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "page_count": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type",
        "kind"
      ],
      "type": "object"
    },
    "FullListBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "full_list",
          "type": "string"
        },
        "list_id": {
          "minLength": 1,
          "type": "string"
        },
        "list_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "list_id"
      ],
      "type": "object"
    },
    "FullTableBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "full_table",
          "type": "string"
        },
        "table_id": {
          "minLength": 1,
          "type": "string"
        },
        "table_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "table_id"
      ],
      "type": "object"
    },
    "GroupPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "group",
          "type": "string"
        },
        "group": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "group"
      ],
      "type": "object"
    },
    "HostedToolPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "hosted_tool",
          "type": "string"
        },
        "provider": {
          "type": "string"
        },
        "block": {
          "additionalProperties": true,
          "type": "object"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "ImageMediaPart": {
      "additionalProperties": false,
      "allOf": [
        {
          "anyOf": [
            {
              "properties": {
                "url": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "url"
              ]
            },
            {
              "properties": {
                "file_id": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "file_id"
              ]
            }
          ]
        }
      ],
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "origin": {
          "anyOf": [
            {
              "enum": [
                "matrx",
                "external"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "file_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "mime_type": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "size_bytes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "visibility": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "cdn_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "media",
          "type": "string"
        },
        "kind": {
          "const": "image",
          "type": "string"
        },
        "width": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "height": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "role": {
          "anyOf": [
            {
              "enum": [
                "subject",
                "character",
                "style",
                "mask",
                "edit_target",
                "composition_control",
                "first_frame",
                "last_frame",
                "asset"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type",
        "kind"
      ],
      "type": "object"
    },
    "ItemPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "item",
          "type": "string"
        },
        "item_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "item_id"
      ],
      "type": "object"
    },
    "JsonValue": {},
    "ListGroupBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "list_group",
          "type": "string"
        },
        "list_id": {
          "minLength": 1,
          "type": "string"
        },
        "group_name": {
          "minLength": 1,
          "type": "string"
        },
        "list_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "list_id",
        "group_name"
      ],
      "type": "object"
    },
    "ListInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_list",
          "type": "string"
        },
        "bookmarks": {
          "items": {
            "discriminator": {
              "mapping": {
                "full_list": "#/$defs/FullListBookmark",
                "list_group": "#/$defs/ListGroupBookmark",
                "list_item": "#/$defs/ListItemBookmark"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/FullListBookmark"
              },
              {
                "$ref": "#/$defs/ListGroupBookmark"
              },
              {
                "$ref": "#/$defs/ListItemBookmark"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "bookmarks",
        "type"
      ],
      "type": "object"
    },
    "ListItemBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "list_item",
          "type": "string"
        },
        "list_id": {
          "minLength": 1,
          "type": "string"
        },
        "item_id": {
          "minLength": 1,
          "type": "string"
        },
        "list_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "list_id",
        "item_id"
      ],
      "type": "object"
    },
    "LiveResourceRefInput": {
      "additionalProperties": true,
      "properties": {
        "id": {
          "minLength": 1,
          "type": "string"
        },
        "mode": {
          "const": "reference",
          "type": "string"
        }
      },
      "required": [
        "id"
      ],
      "type": "object"
    },
    "NormalizedCitation": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "enum": [
            "document_char",
            "document_page",
            "document_block",
            "search_result",
            "web",
            "grounding"
          ],
          "type": "string"
        },
        "provider": {
          "enum": [
            "anthropic",
            "openai",
            "google",
            "xai"
          ],
          "type": "string"
        },
        "cited_text": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "source_index": {
          "type": "integer"
        },
        "file_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "page": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "end_page": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "source_start": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "source_end": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "answer_start": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "answer_end": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "raw": {
          "additionalProperties": true,
          "type": "object"
        }
      },
      "required": [
        "kind",
        "provider"
      ],
      "type": "object"
    },
    "NotesInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_notes",
          "type": "string"
        },
        "note_ids": {
          "items": {
            "anyOf": [
              {
                "minLength": 1,
                "type": "string"
              },
              {
                "$ref": "#/$defs/ResourceRefInput"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        },
        "template": {
          "type": "string"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "note_ids",
        "type"
      ],
      "type": "object"
    },
    "PagePart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "page",
          "type": "string"
        },
        "page": {
          "minimum": 1,
          "type": "integer"
        }
      },
      "required": [
        "scope",
        "page"
      ],
      "type": "object"
    },
    "PreFetchedUrl": {
      "additionalProperties": true,
      "properties": {
        "url": {
          "minLength": 1,
          "type": "string"
        },
        "textContent": {
          "type": "string"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "scrapedAt": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "charCount": {
          "anyOf": [
            {
              "minimum": 0,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "url",
        "textContent"
      ],
      "type": "object"
    },
    "ProjectInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_project",
          "type": "string"
        },
        "project_ids": {
          "items": {
            "minLength": 1,
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "project_ids",
        "type"
      ],
      "type": "object"
    },
    "RemarkAnswer": {
      "additionalProperties": false,
      "properties": {
        "question": {
          "minLength": 1,
          "type": "string"
        },
        "answer": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "boolean"
            },
            {
              "type": "number"
            },
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          ]
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "anyOf": [
            {
              "enum": [
                "noul",
                "choice",
                "score",
                "text"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "question",
        "answer"
      ],
      "type": "object"
    },
    "RemarkItem": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "enum": [
            "comment",
            "choice",
            "edit",
            "answers",
            "interaction"
          ],
          "type": "string"
        },
        "target": {
          "anyOf": [
            {
              "$ref": "#/$defs/RemarkTarget"
            },
            {
              "type": "null"
            }
          ]
        },
        "quote": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "diff": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "answers": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/RemarkAnswer"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "comment_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "id": {
          "anyOf": [
            {
              "maxLength": 200,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "handle": {
          "anyOf": [
            {
              "pattern": "^c[1-9][0-9]{0,5}$",
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "thread": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/RemarkThreadEntry"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "block_state_ref": {
          "anyOf": [
            {
              "$ref": "#/$defs/BlockStateRef"
            },
            {
              "type": "null"
            }
          ]
        },
        "metadata": {
          "additionalProperties": true,
          "type": "object"
        }
      },
      "required": [
        "kind"
      ],
      "type": "object"
    },
    "RemarkTarget": {
      "additionalProperties": false,
      "properties": {
        "message_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "record_token": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "record_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "record_title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "type": "object"
    },
    "RemarkThreadEntry": {
      "additionalProperties": false,
      "properties": {
        "author_name": {
          "maxLength": 200,
          "minLength": 1,
          "type": "string"
        },
        "author_kind": {
          "enum": [
            "person",
            "agent"
          ],
          "type": "string"
        },
        "body": {
          "minLength": 1,
          "type": "string"
        },
        "created_at": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "author_name",
        "author_kind",
        "body"
      ],
      "type": "object"
    },
    "RemarksInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_remarks",
          "type": "string"
        },
        "items": {
          "items": {
            "$ref": "#/$defs/RemarkItem"
          },
          "minItems": 1,
          "type": "array"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "items",
        "type"
      ],
      "type": "object"
    },
    "ResourcePromotion": {
      "additionalProperties": false,
      "properties": {
        "representation": {
          "type": "string"
        },
        "max_chars": {
          "maximum": 10000,
          "minimum": 1,
          "type": "integer"
        }
      },
      "required": [
        "representation"
      ],
      "type": "object"
    },
    "ResourceRefInput": {
      "anyOf": [
        {
          "$ref": "#/$defs/LiveResourceRefInput"
        },
        {
          "$ref": "#/$defs/SnapshotContentResourceRefInput"
        },
        {
          "$ref": "#/$defs/SnapshotTextResourceRefInput"
        },
        {
          "$ref": "#/$defs/SnapshotBodyResourceRefInput"
        },
        {
          "$ref": "#/$defs/SnapshotDescriptionResourceRefInput"
        },
        {
          "$ref": "#/$defs/SnapshotValueResourceRefInput"
        }
      ]
    },
    "RowPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "row",
          "type": "string"
        },
        "row_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "row_id"
      ],
      "type": "object"
    },
    "SchemaPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "schema",
          "type": "string"
        }
      },
      "required": [
        "scope"
      ],
      "type": "object"
    },
    "SheetPart": {
      "additionalProperties": false,
      "properties": {
        "scope": {
          "const": "sheet",
          "type": "string"
        },
        "sheet": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "scope",
        "sheet"
      ],
      "type": "object"
    },
    "SnapshotBodyResourceRefInput": {
      "additionalProperties": true,
      "properties": {
        "mode": {
          "const": "snapshot",
          "type": "string"
        },
        "body": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "mode",
        "body"
      ],
      "type": "object"
    },
    "SnapshotContentResourceRefInput": {
      "additionalProperties": true,
      "properties": {
        "mode": {
          "const": "snapshot",
          "type": "string"
        },
        "content": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "mode",
        "content"
      ],
      "type": "object"
    },
    "SnapshotDescriptionResourceRefInput": {
      "additionalProperties": true,
      "properties": {
        "mode": {
          "const": "snapshot",
          "type": "string"
        },
        "description": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "mode",
        "description"
      ],
      "type": "object"
    },
    "SnapshotTextResourceRefInput": {
      "additionalProperties": true,
      "properties": {
        "mode": {
          "const": "snapshot",
          "type": "string"
        },
        "text": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "mode",
        "text"
      ],
      "type": "object"
    },
    "SnapshotValueResourceRefInput": {
      "additionalProperties": true,
      "properties": {
        "mode": {
          "const": "snapshot",
          "type": "string"
        },
        "value": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "mode",
        "value"
      ],
      "type": "object"
    },
    "SourceRef": {
      "properties": {
        "envelope": {
          "const": "resource_ref",
          "type": "string"
        },
        "resource_type": {
          "maxLength": 64,
          "minLength": 1,
          "type": "string"
        },
        "resource_id": {
          "type": "string"
        },
        "representation": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "include_segments": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "delivery": {
          "anyOf": [
            {
              "enum": [
                "direct",
                "context"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "max_chars": {
          "anyOf": [
            {
              "minimum": 1,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "promote": {
          "anyOf": [
            {
              "$ref": "#/$defs/ResourcePromotion"
            },
            {
              "items": {
                "$ref": "#/$defs/ResourcePromotion"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "exclude": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ]
        },
        "label": {
          "anyOf": [
            {
              "maxLength": 500,
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "overlay": {
          "anyOf": [
            {
              "additionalProperties": {
                "$ref": "#/$defs/JsonValue"
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ]
        },
        "part": {
          "anyOf": [
            {
              "discriminator": {
                "mapping": {
                  "cell": "#/$defs/CellPart",
                  "column": "#/$defs/ColumnPart",
                  "context_cell": "#/$defs/ContextCellPart",
                  "group": "#/$defs/GroupPart",
                  "item": "#/$defs/ItemPart",
                  "page": "#/$defs/PagePart",
                  "row": "#/$defs/RowPart",
                  "schema": "#/$defs/SchemaPart",
                  "sheet": "#/$defs/SheetPart"
                },
                "propertyName": "scope"
              },
              "oneOf": [
                {
                  "$ref": "#/$defs/ColumnPart"
                },
                {
                  "$ref": "#/$defs/RowPart"
                },
                {
                  "$ref": "#/$defs/CellPart"
                },
                {
                  "$ref": "#/$defs/SchemaPart"
                },
                {
                  "$ref": "#/$defs/GroupPart"
                },
                {
                  "$ref": "#/$defs/ItemPart"
                },
                {
                  "$ref": "#/$defs/PagePart"
                },
                {
                  "$ref": "#/$defs/SheetPart"
                },
                {
                  "$ref": "#/$defs/ContextCellPart"
                }
              ]
            },
            {
              "type": "null"
            }
          ]
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "keep_fresh": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "optional_context": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "persistence": {
          "anyOf": [
            {
              "enum": [
                "durable",
                "turn"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "resource_type",
        "resource_id"
      ],
      "type": "object"
    },
    "SourceSet": {
      "properties": {
        "envelope": {
          "const": "source_set",
          "type": "string"
        },
        "version": {
          "const": 1,
          "type": "integer"
        },
        "sources": {
          "items": {
            "$ref": "#/$defs/SourceRef"
          },
          "type": "array"
        },
        "topic": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "grounding": {
          "enum": [
            "whole",
            "selected",
            "retrieve"
          ],
          "type": "string"
        },
        "retrieve_query": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "target_model_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "type": "object"
    },
    "SourceSetPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "source_set",
          "type": "string"
        },
        "source_set": {
          "$ref": "#/$defs/SourceSet"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "source_set",
        "type"
      ],
      "type": "object"
    },
    "SpeechScriptPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "speech_script",
          "type": "string"
        },
        "__kind": {
          "const": "speech_script",
          "type": "string"
        },
        "turns": {
          "items": {
            "$ref": "#/$defs/SpeechTurn"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "turns",
        "type"
      ],
      "type": "object"
    },
    "SpeechTurn": {
      "additionalProperties": false,
      "properties": {
        "__kind": {
          "const": "",
          "type": "string"
        },
        "speaker": {
          "maxLength": 40,
          "minLength": 1,
          "type": "string"
        },
        "voice": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "text": {
          "minLength": 1,
          "type": "string"
        },
        "direction": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "pause_after_ms": {
          "anyOf": [
            {
              "maximum": 10000,
              "minimum": 0,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "speaker",
        "text"
      ],
      "type": "object"
    },
    "TableCellBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "table_cell",
          "type": "string"
        },
        "table_id": {
          "minLength": 1,
          "type": "string"
        },
        "row_id": {
          "minLength": 1,
          "type": "string"
        },
        "column_name": {
          "minLength": 1,
          "type": "string"
        },
        "table_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "table_id",
        "row_id",
        "column_name"
      ],
      "type": "object"
    },
    "TableColumnBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "table_column",
          "type": "string"
        },
        "table_id": {
          "minLength": 1,
          "type": "string"
        },
        "column_name": {
          "minLength": 1,
          "type": "string"
        },
        "table_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "table_id",
        "column_name"
      ],
      "type": "object"
    },
    "TableInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_table",
          "type": "string"
        },
        "bookmarks": {
          "items": {
            "discriminator": {
              "mapping": {
                "full_table": "#/$defs/FullTableBookmark",
                "table_cell": "#/$defs/TableCellBookmark",
                "table_column": "#/$defs/TableColumnBookmark",
                "table_row": "#/$defs/TableRowBookmark",
                "table_schema": "#/$defs/TableSchemaBookmark"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/FullTableBookmark"
              },
              {
                "$ref": "#/$defs/TableColumnBookmark"
              },
              {
                "$ref": "#/$defs/TableRowBookmark"
              },
              {
                "$ref": "#/$defs/TableCellBookmark"
              },
              {
                "$ref": "#/$defs/TableSchemaBookmark"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "bookmarks",
        "type"
      ],
      "type": "object"
    },
    "TableRowBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "table_row",
          "type": "string"
        },
        "table_id": {
          "minLength": 1,
          "type": "string"
        },
        "row_id": {
          "minLength": 1,
          "type": "string"
        },
        "table_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "table_id",
        "row_id"
      ],
      "type": "object"
    },
    "TableSchemaBookmark": {
      "additionalProperties": true,
      "properties": {
        "type": {
          "const": "table_schema",
          "type": "string"
        },
        "table_id": {
          "minLength": 1,
          "type": "string"
        },
        "table_name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "table_id"
      ],
      "type": "object"
    },
    "TaskInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_task",
          "type": "string"
        },
        "task_ids": {
          "items": {
            "anyOf": [
              {
                "minLength": 1,
                "type": "string"
              },
              {
                "$ref": "#/$defs/ResourceRefInput"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        },
        "template": {
          "type": "string"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "task_ids",
        "type"
      ],
      "type": "object"
    },
    "TextPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "text",
          "type": "string"
        },
        "text": {
          "type": "string"
        },
        "id": {
          "type": "string"
        },
        "citations": {
          "items": {
            "$ref": "#/$defs/NormalizedCitation"
          },
          "type": "array"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "ThinkingPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "thinking",
          "type": "string"
        },
        "text": {
          "type": "string"
        },
        "id": {
          "type": "string"
        },
        "provider": {
          "anyOf": [
            {
              "enum": [
                "openai",
                "anthropic",
                "google",
                "cerebras",
                "moonshot",
                "together",
                "groq",
                "xai",
                "generic_openai"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "signature": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "signature_encoding": {
          "anyOf": [
            {
              "const": "base64",
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "summary": {
          "items": {},
          "type": "array"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "ToolCallPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "tool_call",
          "type": "string"
        },
        "call_id": {
          "type": "string"
        },
        "name": {
          "type": "string"
        },
        "arguments": {
          "additionalProperties": true,
          "type": "object"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "ToolResultPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "tool_result",
          "type": "string"
        },
        "call_id": {
          "type": "string"
        },
        "tool_use_id": {
          "type": "string"
        },
        "name": {
          "type": "string"
        },
        "is_error": {
          "type": "boolean"
        },
        "output_chars": {
          "type": "integer"
        },
        "output_preview": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "TranscriptInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_transcript",
          "type": "string"
        },
        "transcript_ids": {
          "items": {
            "minLength": 1,
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "transcript_ids",
        "type"
      ],
      "type": "object"
    },
    "TranscriptSessionInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_transcript_session",
          "type": "string"
        },
        "transcript_session_ids": {
          "items": {
            "minLength": 1,
            "type": "string"
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "transcript_session_ids",
        "type"
      ],
      "type": "object"
    },
    "VideoMediaPart": {
      "additionalProperties": false,
      "allOf": [
        {
          "anyOf": [
            {
              "properties": {
                "url": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "url"
              ]
            },
            {
              "properties": {
                "file_id": {
                  "minLength": 1,
                  "type": "string"
                }
              },
              "required": [
                "file_id"
              ]
            }
          ]
        }
      ],
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "origin": {
          "anyOf": [
            {
              "enum": [
                "matrx",
                "external"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "file_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "mime_type": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "size_bytes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "visibility": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "cdn_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "media",
          "type": "string"
        },
        "kind": {
          "const": "video",
          "type": "string"
        },
        "width": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "height": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "duration_ms": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        },
        "role": {
          "anyOf": [
            {
              "enum": [
                "extend",
                "restyle"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "name": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "type",
        "kind"
      ],
      "type": "object"
    },
    "WebSearchPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "web_search",
          "type": "string"
        },
        "id": {
          "type": "string"
        },
        "status": {
          "type": "string"
        }
      },
      "required": [
        "type"
      ],
      "type": "object"
    },
    "WebpageInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "input_webpage",
          "type": "string"
        },
        "urls": {
          "items": {
            "anyOf": [
              {
                "minLength": 1,
                "type": "string"
              },
              {
                "$ref": "#/$defs/PreFetchedUrl"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "urls",
        "type"
      ],
      "type": "object"
    },
    "WorkbookInputPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "convert_to_text": {
          "type": "boolean"
        },
        "optional_context": {
          "type": "boolean"
        },
        "keep_fresh": {
          "type": "boolean"
        },
        "editable": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ]
        },
        "template": {
          "anyOf": [
            {
              "enum": [
                "full",
                "compact",
                "minimal"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "type": {
          "const": "input_workbook",
          "type": "string"
        },
        "workbook_ids": {
          "items": {
            "anyOf": [
              {
                "minLength": 1,
                "type": "string"
              },
              {
                "$ref": "#/$defs/LiveResourceRefInput"
              }
            ]
          },
          "minItems": 1,
          "type": "array"
        }
      },
      "required": [
        "workbook_ids",
        "type"
      ],
      "type": "object"
    },
    "YouTubeMediaPart": {
      "additionalProperties": false,
      "properties": {
        "metadata": {
          "additionalProperties": {
            "$ref": "#/$defs/JsonValue"
          },
          "type": "object"
        },
        "type": {
          "const": "media",
          "type": "string"
        },
        "kind": {
          "const": "youtube",
          "type": "string"
        },
        "url": {
          "minLength": 1,
          "type": "string"
        },
        "external_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "origin": {
          "const": "external",
          "type": "string"
        },
        "file_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "mime_type": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "size_bytes": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "url",
        "type",
        "kind"
      ],
      "type": "object"
    }
  },
  "anyOf": [
    {
      "$ref": "#/$defs/TextPart"
    },
    {
      "$ref": "#/$defs/ThinkingPart"
    },
    {
      "$ref": "#/$defs/ToolCallPart"
    },
    {
      "$ref": "#/$defs/ToolResultPart"
    },
    {
      "discriminator": {
        "mapping": {
          "audio": "#/$defs/AudioMediaPart",
          "document": "#/$defs/DocumentMediaPart",
          "image": "#/$defs/ImageMediaPart",
          "video": "#/$defs/VideoMediaPart",
          "youtube": "#/$defs/YouTubeMediaPart"
        },
        "propertyName": "kind"
      },
      "oneOf": [
        {
          "$ref": "#/$defs/ImageMediaPart"
        },
        {
          "$ref": "#/$defs/AudioMediaPart"
        },
        {
          "$ref": "#/$defs/VideoMediaPart"
        },
        {
          "$ref": "#/$defs/DocumentMediaPart"
        },
        {
          "$ref": "#/$defs/YouTubeMediaPart"
        }
      ]
    },
    {
      "$ref": "#/$defs/CodeExecPart"
    },
    {
      "$ref": "#/$defs/CodeResultPart"
    },
    {
      "$ref": "#/$defs/WebSearchPart"
    },
    {
      "$ref": "#/$defs/HostedToolPart"
    },
    {
      "$ref": "#/$defs/WebpageInputPart"
    },
    {
      "$ref": "#/$defs/NotesInputPart"
    },
    {
      "$ref": "#/$defs/TaskInputPart"
    },
    {
      "$ref": "#/$defs/AgentInputPart"
    },
    {
      "$ref": "#/$defs/ProjectInputPart"
    },
    {
      "$ref": "#/$defs/AgentAppInputPart"
    },
    {
      "$ref": "#/$defs/TranscriptInputPart"
    },
    {
      "$ref": "#/$defs/TranscriptSessionInputPart"
    },
    {
      "$ref": "#/$defs/WorkbookInputPart"
    },
    {
      "$ref": "#/$defs/DocumentInputPart"
    },
    {
      "$ref": "#/$defs/TableInputPart"
    },
    {
      "$ref": "#/$defs/ListInputPart"
    },
    {
      "$ref": "#/$defs/DataInputPart"
    },
    {
      "$ref": "#/$defs/ContextInputPart"
    },
    {
      "$ref": "#/$defs/RemarksInputPart"
    },
    {
      "$ref": "#/$defs/SourceSetPart"
    },
    {
      "$ref": "#/$defs/DecisionQuestionsPart"
    },
    {
      "$ref": "#/$defs/DecisionAnswersPart"
    },
    {
      "$ref": "#/$defs/SpeechScriptPart"
    }
  ]
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function schemaTypeMatches(type: MessagePartJsonSchema["type"], value: unknown): boolean {
  if (type === "null") return value === null;
  if (type === "array") return Array.isArray(value);
  if (type === "object") return isRecord(value);
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  return typeof value === type;
}

function resolveMessagePartSchemaRef(
  ref: string,
  root: MessagePartJsonSchema,
): MessagePartJsonSchema | undefined {
  const prefix = "#/$defs/";
  if (!ref.startsWith(prefix)) return undefined;
  return root.$defs?.[ref.slice(prefix.length)];
}

function matchesMessagePartSchema(
  value: unknown,
  schema: MessagePartJsonSchema,
  root: MessagePartJsonSchema,
): boolean {
  if (schema.$ref) {
    const resolved = resolveMessagePartSchemaRef(schema.$ref, root);
    return resolved !== undefined && matchesMessagePartSchema(value, resolved, root);
  }
  if (schema.anyOf && !schema.anyOf.some((item) => matchesMessagePartSchema(value, item, root))) {
    return false;
  }
  if (schema.oneOf) {
    const matches = schema.oneOf.filter((item) => matchesMessagePartSchema(value, item, root));
    if (matches.length !== 1) return false;
  }
  if (schema.allOf && !schema.allOf.every((item) => matchesMessagePartSchema(value, item, root))) {
    return false;
  }
  if ("const" in schema && !Object.is(value, schema.const)) return false;
  if (schema.enum && !schema.enum.some((item) => Object.is(value, item))) return false;
  if (schema.type && !schemaTypeMatches(schema.type, value)) return false;
  if (schema.type === "string" && typeof value === "string" &&
      schema.minLength !== undefined && value.length < schema.minLength) return false;
  if (schema.type === "array" && Array.isArray(value) &&
      schema.minItems !== undefined && value.length < schema.minItems) return false;
  if ((schema.type === "number" || schema.type === "integer") && typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) return false;
    if (schema.maximum !== undefined && value > schema.maximum) return false;
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) return false;
    if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) return false;
  }
  if (schema.type === "array" && schema.items && Array.isArray(value)) {
    return value.every((item) => matchesMessagePartSchema(item, schema.items!, root));
  }
  const hasObjectKeywords =
    schema.type === "object" ||
    schema.required !== undefined ||
    schema.properties !== undefined ||
    schema.additionalProperties !== undefined;
  if (!hasObjectKeywords) return true;
  if (!isRecord(value)) return false;
  if (schema.required?.some((key) => !(key in value))) return false;
  const properties = schema.properties ?? {};
  for (const [key, propertySchema] of Object.entries(properties)) {
    if (key in value && !matchesMessagePartSchema(value[key], propertySchema, root)) {
      return false;
    }
  }
  const extraKeys = Object.keys(value).filter((key) => !(key in properties));
  if (schema.additionalProperties === false && extraKeys.length > 0) return false;
  const additionalSchema = schema.additionalProperties;
  if (isRecord(additionalSchema)) {
    return extraKeys.every((key) =>
      matchesMessagePartSchema(value[key], additionalSchema, root),
    );
  }
  return true;
}

/** Runtime guard for persisted chat.message.content entries. */
export function isMessagePart(value: unknown): value is MessagePart {
  return matchesMessagePartSchema(value, MESSAGE_PART_SCHEMA, MESSAGE_PART_SCHEMA);
}

/** Validate and parse the content array from a chat.message DB row. */
export function parseMessageContent(content: unknown[]): MessagePart[] {
  return content.map((part, index) => {
    if (!isMessagePart(part)) {
      throw new TypeError(
        `Invalid chat.message.content[${index}]: unknown or malformed message part`,
      );
    }
    return part;
  });
}

// --- Message wrapper (matrx_ai.config.message_flags) ---

/** The translator flags a message can carry. */
export const MESSAGE_FLAG_KEYS = ["prefill", "example"] as const;
export type MessageFlagKey = (typeof MESSAGE_FLAG_KEYS)[number];
/** Stored form (`MessageFlags.as_dict`): only `true` means anything; absence is false. */
export type MessageFlags = Partial<Record<MessageFlagKey, true>>;
/** Where a runtime message keeps its flags: `cx_message.metadata[MESSAGE_FLAGS_METADATA_KEY]`. */
export const MESSAGE_FLAGS_METADATA_KEY = "flags";
/** What a flag the model cannot honor becomes (org knob agents.messages / flag_compatibility_mode). */
export const FLAG_COMPATIBILITY_MODES = ["refuse", "convert", "drop"] as const;
export type FlagCompatibilityMode = (typeof FLAG_COMPATIBILITY_MODES)[number];
export const DEFAULT_FLAG_COMPATIBILITY_MODE: FlagCompatibilityMode = "convert";

/** One message: its role, its parts, and its translator flags (top level on an
 *  agent-definition message; `metadata.flags` on a runtime cx_message). */
export interface MessageWrapper {
  role: string;
  content: MessagePart[];
  flags?: MessageFlags | null;
}


export interface ChunkEvent {
  event: "chunk";
  data: ChunkPayload;
}

export interface ReasoningChunkEvent {
  event: "reasoning_chunk";
  data: ReasoningChunkPayload;
}

export interface ReasoningEvent {
  event: "reasoning";
  data: ReasoningPayload;
}

export interface PhaseEvent {
  event: "phase";
  data: PhasePayload;
}

export interface WarningEvent {
  event: "warning";
  data: WarningPayload;
}

export interface InfoEvent {
  event: "info";
  data: InfoPayload;
}

export interface TypedDataEvent {
  event: "data";
  data: TypedDataPayload | UntypedDataPayload;
}

export interface InitEvent {
  event: "init";
  data: InitPayload;
}

export interface CompletionEvent {
  event: "completion";
  data: CompletionPayload;
}

export interface ErrorEvent {
  event: "error";
  data: ErrorPayload;
}

export interface ToolEventEvent {
  event: "tool_event";
  data: ToolEventPayload;
}

export interface HeartbeatEvent {
  event: "heartbeat";
  data: HeartbeatPayload;
}

export interface EndEvent {
  event: "end";
  data: EndPayload;
}

export interface RenderBlockEvent {
  event: "render_block";
  data: RenderBlockPayload;
}

export interface RecordReservedEvent {
  event: "record_reserved";
  data: RecordReservedPayload;
}

export interface RecordUpdateEvent {
  event: "record_update";
  data: RecordUpdatePayload;
}

export interface ResourceChangedEvent {
  event: "resource_changed";
  data: ResourceChangedPayload;
}

export interface ContextAnalysisEvent {
  event: "context_analysis";
  data: ContextAnalysisPayload;
}

export interface StructuredOutputEvent {
  event: "structured_output";
  data: StructuredOutputPayload;
}

export interface ContextStateEvent {
  event: "context_state";
  data: ContextStatePayload;
}

export interface ContextTrimmedEvent {
  event: "context_trimmed";
  data: ContextTrimmedPayload;
}

export interface InjectionConsumedEvent {
  event: "injection_consumed";
  data: InjectionConsumedPayload;
}

export interface ProviderRetryEvent {
  event: "provider_retry";
  data: ProviderRetryPayload;
}

export interface CitationEvent {
  event: "citation";
  data: CitationPayload;
}

export interface ControlTokenEvent {
  event: "control_token";
  data: ControlTokenPayload;
}

/** Discriminated union — `event.event === "chunk"` narrows `data` automatically. */
export type TypedStreamEvent =
  | ChunkEvent
  | ReasoningChunkEvent
  | ReasoningEvent
  | PhaseEvent
  | WarningEvent
  | InfoEvent
  | TypedDataEvent
  | InitEvent
  | CompletionEvent
  | ErrorEvent
  | ToolEventEvent
  | HeartbeatEvent
  | EndEvent
  | RenderBlockEvent
  | RecordReservedEvent
  | RecordUpdateEvent
  | ResourceChangedEvent
  | ContextAnalysisEvent
  | StructuredOutputEvent
  | ContextStateEvent
  | ContextTrimmedEvent
  | InjectionConsumedEvent
  | ProviderRetryEvent
  | CitationEvent
  | ControlTokenEvent;

/**
 * @deprecated Use `TypedStreamEvent` instead — it provides automatic type narrowing
 * via the discriminated union so `event.event === "chunk"` narrows `data` to `ChunkPayload`.
 */
export type StreamEvent = TypedStreamEvent;

// Compact wire format for high-frequency events (90%+ of stream traffic).
// e = event type ("c" = chunk, "r" = reasoning_chunk), t = text content.
export interface CompactChunkEvent {
  e: "c";
  t: string;
}

export interface CompactReasoningChunkEvent {
  e: "r";
  t: string;
}

export type CompactStreamEvent = CompactChunkEvent | CompactReasoningChunkEvent;

/** A line from the NDJSON stream — either compact or standard format. */
export type RawStreamLine = CompactStreamEvent | TypedStreamEvent;

export function isCompactEvent(line: unknown): line is CompactStreamEvent {
  return typeof line === "object" && line !== null && "e" in line && "t" in line;
}

/** Normalize a compact event into the standard TypedStreamEvent shape. */
export function expandCompactEvent(compact: CompactStreamEvent): TypedStreamEvent {
  if (compact.e === "c") return { event: "chunk", data: { text: compact.t } };
  return { event: "reasoning_chunk", data: { text: compact.t } };
}

// Type guards (work on both TypedStreamEvent and the deprecated StreamEvent alias)
export function isChunkEvent(e: TypedStreamEvent): e is { event: "chunk"; data: ChunkPayload } {
  return e.event === "chunk";
}

export function isReasoningChunkEvent(e: TypedStreamEvent): e is { event: "reasoning_chunk"; data: ReasoningChunkPayload } {
  return e.event === "reasoning_chunk";
}

export function isReasoningEvent(e: TypedStreamEvent): e is { event: "reasoning"; data: ReasoningPayload } {
  return e.event === "reasoning";
}

export function isPhaseEvent(e: TypedStreamEvent): e is { event: "phase"; data: PhasePayload } {
  return e.event === "phase";
}

export function isWarningEvent(e: TypedStreamEvent): e is { event: "warning"; data: WarningPayload } {
  return e.event === "warning";
}

export function isInfoEvent(e: TypedStreamEvent): e is { event: "info"; data: InfoPayload } {
  return e.event === "info";
}

export function isTypedDataEvent(e: TypedStreamEvent): e is { event: "data"; data: TypedDataPayload | UntypedDataPayload } {
  return e.event === "data";
}

export function isInitEvent(e: TypedStreamEvent): e is { event: "init"; data: InitPayload } {
  return e.event === "init";
}

export function isCompletionEvent(e: TypedStreamEvent): e is { event: "completion"; data: CompletionPayload } {
  return e.event === "completion";
}

export function isErrorEvent(e: TypedStreamEvent): e is { event: "error"; data: ErrorPayload } {
  return e.event === "error";
}

export function isToolEventEvent(e: TypedStreamEvent): e is { event: "tool_event"; data: ToolEventPayload } {
  return e.event === "tool_event";
}

export function isHeartbeatEvent(e: TypedStreamEvent): e is { event: "heartbeat"; data: HeartbeatPayload } {
  return e.event === "heartbeat";
}

export function isEndEvent(e: TypedStreamEvent): e is { event: "end"; data: EndPayload } {
  return e.event === "end";
}

export function isRenderBlockEvent(e: TypedStreamEvent): e is { event: "render_block"; data: RenderBlockPayload } {
  return e.event === "render_block";
}

export function isRecordReservedEvent(e: TypedStreamEvent): e is { event: "record_reserved"; data: RecordReservedPayload } {
  return e.event === "record_reserved";
}

export function isRecordUpdateEvent(e: TypedStreamEvent): e is { event: "record_update"; data: RecordUpdatePayload } {
  return e.event === "record_update";
}

export function isResourceChangedEvent(e: TypedStreamEvent): e is { event: "resource_changed"; data: ResourceChangedPayload } {
  return e.event === "resource_changed";
}

export function isContextAnalysisEvent(e: TypedStreamEvent): e is { event: "context_analysis"; data: ContextAnalysisPayload } {
  return e.event === "context_analysis";
}

export function isStructuredOutputEvent(e: TypedStreamEvent): e is { event: "structured_output"; data: StructuredOutputPayload } {
  return e.event === "structured_output";
}

export function isContextStateEvent(e: TypedStreamEvent): e is { event: "context_state"; data: ContextStatePayload } {
  return e.event === "context_state";
}

export function isContextTrimmedEvent(e: TypedStreamEvent): e is { event: "context_trimmed"; data: ContextTrimmedPayload } {
  return e.event === "context_trimmed";
}

export function isInjectionConsumedEvent(e: TypedStreamEvent): e is { event: "injection_consumed"; data: InjectionConsumedPayload } {
  return e.event === "injection_consumed";
}

export function isProviderRetryEvent(e: TypedStreamEvent): e is { event: "provider_retry"; data: ProviderRetryPayload } {
  return e.event === "provider_retry";
}

export function isCitationEvent(e: TypedStreamEvent): e is { event: "citation"; data: CitationPayload } {
  return e.event === "citation";
}

export function isControlTokenEvent(e: TypedStreamEvent): e is { event: "control_token"; data: ControlTokenPayload } {
  return e.event === "control_token";
}

export function isCompactChunkEvent(e: unknown): e is CompactChunkEvent {
  return typeof e === "object" && e !== null && (e as CompactChunkEvent).e === "c" && typeof (e as CompactChunkEvent).t === "string";
}

export function isCompactReasoningChunkEvent(e: unknown): e is CompactReasoningChunkEvent {
  return typeof e === "object" && e !== null && (e as CompactReasoningChunkEvent).e === "r" && typeof (e as CompactReasoningChunkEvent).t === "string";
}
