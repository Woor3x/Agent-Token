export interface AuditEvent {
  event_id: string;
  timestamp: string;
  event_type: string;
  trace_id?: string;
  span_id?: string;
  parent_span_id?: string;
  plan_id?: string;
  task_id?: string;
  decision?: string;
  deny_reasons?: string[];
  caller_agent?: string;
  caller_sub?: string;
  caller_jti?: string;
  callee_agent?: string;
  callee_action?: string;
  callee_resource?: string;
  latency_ms?: number;
  revoke_type?: string;
  revoke_value?: string;
  revoke_reason?: string;
  anomaly_rule?: string;
  severity?: string;
  purpose?: string;
  policy_version?: string;
}

export interface TraceSpan {
  span_id: string;
  parent_span_id?: string;
  caller?: string;
  callee?: string;
  decision?: string;
  latency_ms?: number;
  event_id: string;
  children: TraceSpan[];
}

export interface TraceResult {
  trace_id: string;
  started_at?: string;
  ended_at?: string;
  total_spans: number;
  decisions: Record<string, number>;
  spans: TraceSpan[];
}

export interface PlanTask {
  task_id?: string;
  agent?: string;
  action?: string;
  jti?: string;
  issued_at?: string;
  consumed_at?: string;
  decision?: string;
  latency_ms?: number;
}

export interface PlanResult {
  plan_id: string;
  user?: string;
  orchestrator?: string;
  tasks: PlanTask[];
  summary: { total: number; allow: number; deny: number };
}

export interface AgentInfo {
  agent_id: string;
  role: string;
  kid: string;
  status: string;
  display_name?: string;
  contact?: string;
  registered_at: string;
  registered_by?: string;
}

export interface DagTask {
  id: string;
  agent: string;
  action: string;
  resource: string;
  params?: Record<string, string>;
  deps: string[];
}

export interface ChatResponse {
  status: string;
  trace_id: string;
  plan_id: string;
  dag: DagTask[];
  results: Record<string, unknown>;
  doc?:
    | {
        document_id: string;
        url: string;
        storage?: string;
        title?: string;
        block_count?: number;
      }
    | string;
}

export interface AuditStats {
  window: string;
  total: number;
  by_decision: Record<string, number>;
  by_agent: Record<string, Record<string, number>>;
  by_reason: Record<string, number>;
  tokens_issued: number;
  tokens_consumed: number;
  revoke_events: number;
  anomaly_events: number;
}
