import type { GraphResponse, HealthResult, NodeRecord, NodeResponse } from '../src/types/api';
import { DEMO_LIMITS, type DemoInfo } from '../src/types/demo';
export const INITIAL_RUN = 'synthetic-test-run-1';
export const NEXT_RUN = 'synthetic-test-run-2';
export function makeNode(gid: string): NodeRecord {
  return {
    gid,
    component_id: 'synthetic-component',
    cluster_id: 'synthetic-cluster',
    role: 'peripheral',
    role_score: 0,
    priority_score: 0,
    assignment_status: 'insufficient_evidence',
    metrics: {
      in_degree_unique: 0,
      out_degree_unique: 0,
      tx_in_count: 0,
      tx_out_count: 0,
      in_amount_kzt: '0.00',
      out_amount_kzt: '0.00',
      out_in_ratio: null,
    },
    quality: {
      is_seed: null,
      hop_depth: 0,
      outbound_censored: null,
      inbound_incomplete: null,
      reasons: ['Искусственный пример для проверки интерфейса.'],
    },
    role_evidence: [],
    priority_evidence: [],
    role_explanation: 'Недостаточно наблюдаемых данных для более специфической роли.',
    priority_explanation: 'Искусственное объяснение для теста.',
  };
}

export function nodeResponse(gid: string, runId = INITIAL_RUN): NodeResponse {
  return { contract_version: '1.0', run_id: runId, node: makeNode(gid) };
}

export function graphResponse(gid: string, runId = INITIAL_RUN): GraphResponse {
  return {
    contract_version: '1.0',
    run_id: runId,
    nodes: [makeNode(gid)],
    edges: [],
    truncated: false,
    total_nodes: 1,
    shown_nodes: 1,
  };
}

export function healthResponse(runId = INITIAL_RUN): HealthResult {
  return { status: 'ok', contract_version: '1.0', run_id: runId, data_ready: true, dataSource: 'fixtures' };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

export function demoInfo(runId = INITIAL_RUN): DemoInfo { return { contract_version: '1.0', run_id: runId, enabled: true, node_count: 500, transfer_count: 6000, nodes: [{ gid: '0007', display_name: 'Александр Иванов' }, { gid: '7', display_name: 'Александр Иванов' }, { gid: 'outside-top', display_name: 'Айдана Садыкова' }], groups: [{ id: 'g1', name: 'Тестовая группа' }], limits: DEMO_LIMITS }; }
