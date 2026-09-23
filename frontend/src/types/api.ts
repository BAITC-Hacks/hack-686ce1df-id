/** Derived from docs/contracts/money-graph-v1.md, contract version 1.0. */
export type Role =
  | 'consolidator'
  | 'transit'
  | 'distributor'
  | 'terminal'
  | 'coordinator'
  | 'peripheral';

export interface Evidence {
  rule_id: string;
  metric: string;
  operator: 'gt' | 'gte' | 'lt' | 'lte' | 'eq';
  actual: number | string | boolean | null;
  threshold: number | string | boolean;
  passed: boolean | null;
}

export interface NodeRecord {
  gid: string;
  component_id: string;
  cluster_id: string;
  role: Role;
  role_score: number;
  priority_score: number;
  assignment_status: 'rule_matched' | 'insufficient_evidence';
  metrics: {
    in_degree_unique: number;
    out_degree_unique: number;
    tx_in_count: number;
    tx_out_count: number;
    in_amount_kzt: string;
    out_amount_kzt: string;
    out_in_ratio: number | null;
  };
  quality: {
    is_seed: boolean | null;
    hop_depth: number | null;
    outbound_censored: boolean | null;
    inbound_incomplete: boolean | null;
    reasons: string[];
  };
  role_evidence: Evidence[];
  priority_evidence: Evidence[];
  role_explanation: string;
  priority_explanation: string;
}

export interface EdgeRecord {
  source: string;
  target: string;
  amount_kzt: string;
  tx_count: number;
}

export interface ClusterRecord {
  cluster_id: string;
  component_id: string;
  gids: string[];
  hypothesis: {
    text: string;
    basis_rule_ids: string[];
    limitations: string[];
  };
}

interface ResponseEnvelope {
  contract_version: '1.0';
  run_id: string;
}

export interface HealthResponse {
  status: 'ok';
  contract_version: '1.0';
  run_id: string | null;
  data_ready: boolean;
}

export type DataSource = 'fixtures' | 'artifacts' | 'unavailable';

/** Client metadata from response headers; not part of the health JSON contract. */
export interface HealthResult extends HealthResponse {
  dataSource: DataSource | null;
}

export interface NodeResponse extends ResponseEnvelope {
  node: NodeRecord;
}

export interface PrioritiesResponse extends ResponseEnvelope {
  items: NodeRecord[];
  total: number;
}

export interface ClustersResponse extends ResponseEnvelope {
  items: ClusterRecord[];
}

export interface ClusterResponse extends ResponseEnvelope {
  cluster: ClusterRecord;
}

export interface GraphResponse extends ResponseEnvelope {
  nodes: NodeRecord[];
  edges: EdgeRecord[];
  truncated: boolean;
  total_nodes: number;
  shown_nodes: number;
}

export interface AIResponse extends ResponseEnvelope {
  status: 'ok' | 'fallback';
  summary: string;
  claims: { text: string; evidence_ids: string[] }[];
  limitations: string[];
  checks: {
    tool: string;
    args: Record<string, unknown>;
    result: Record<string, unknown>;
    evidence_id: string;
  }[];
  fallback_reason: string | null;
}

export interface Target {
  kind: 'node' | 'cluster';
  id: string;
}

/** The API accepts exactly one area selector; radius applies to a node only. */
export type GraphQuery = (
  | { gid: string; cluster_id?: never; component_id?: never; radius?: 1 | 2 }
  | { gid?: never; cluster_id: string; component_id?: never; radius?: never }
  | { gid?: never; cluster_id?: never; component_id: string; radius?: never }
) & { limit?: number };
