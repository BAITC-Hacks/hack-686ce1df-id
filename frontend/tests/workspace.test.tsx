// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../src/api/client';
import { formatMoney, formatNumber, formatValue } from '../src/format';
import { useWorkspace } from '../src/state/useWorkspace';
import type { GraphResponse, HealthResult, NodeRecord, NodeResponse } from '../src/types/api';

const INITIAL_RUN = 'synthetic-test-run-1';
const NEXT_RUN = 'synthetic-test-run-2';

// Artificial contract records local to this test. They are not application data.
function makeNode(gid: string): NodeRecord {
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

function nodeResponse(gid: string, runId = INITIAL_RUN): NodeResponse {
  return { contract_version: '1.0', run_id: runId, node: makeNode(gid) };
}

function graphResponse(gid: string, runId = INITIAL_RUN): GraphResponse {
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

function healthResponse(runId = INITIAL_RUN): HealthResult {
  return { status: 'ok', contract_version: '1.0', run_id: runId, data_ready: true, dataSource: 'fixtures' };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

async function readyWorkspace() {
  const view = renderHook(() => useWorkspace());
  await waitFor(() => expect(view.result.current.dataset?.runId).toBe(INITIAL_RUN));
  expect(view.result.current.loading).toBe(false);
  return view;
}

beforeEach(() => {
  vi.spyOn(api, 'health').mockResolvedValue(healthResponse());
  vi.spyOn(api, 'priorities').mockResolvedValue({
    contract_version: '1.0', run_id: INITIAL_RUN, items: [makeNode('0007')], total: 1,
  });
  vi.spyOn(api, 'clusters').mockResolvedValue({
    contract_version: '1.0', run_id: INITIAL_RUN, items: [],
  });
  vi.spyOn(api, 'node').mockImplementation(async (gid) => nodeResponse(gid));
  vi.spyOn(api, 'graph').mockImplementation(async (query) => {
    if (query.gid === undefined) throw new Error('This test only requests node neighborhoods.');
    return graphResponse(query.gid);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('workspace selection across asynchronous API responses', () => {
  it('keeps the current card during a radius refresh and ignores the active radius', async () => {
    const { result } = await readyWorkspace();
    await act(async () => { await result.current.select({ kind: 'node', id: '0007' }); });
    const card = result.current.selection.node;
    const graph = deferred<GraphResponse>();
    vi.mocked(api.graph).mockReturnValueOnce(graph.promise);

    act(() => result.current.changeRadius(1));
    expect(api.graph).toHaveBeenCalledTimes(1);
    act(() => result.current.changeRadius(2));
    expect(result.current.selection.loading).toBe(true);
    expect(result.current.selection.node).toBe(card);
    expect(api.graph).toHaveBeenLastCalledWith({ gid: '0007', radius: 2, limit: 300 }, expect.any(AbortSignal));

    await act(async () => { graph.resolve(graphResponse('0007')); });
    expect(result.current.selection.loading).toBe(false);
    expect(result.current.selection.node?.gid).toBe('0007');
    act(() => result.current.changeRadius(2));
    expect(api.graph).toHaveBeenCalledTimes(2);
  });

  it('keeps a known card after a transient refresh failure but clears it for another target', async () => {
    const { result } = await readyWorkspace();
    await act(async () => { await result.current.select({ kind: 'node', id: '0007' }); });
    const card = result.current.selection.node;
    vi.mocked(api.graph).mockRejectedValueOnce(new ApiError('Сеть недоступна', 0, 'NETWORK_ERROR'));
    await act(async () => result.current.changeRadius(2));
    expect(result.current.selection.node).toBe(card);
    expect(result.current.selection.graph).toBeNull();
    expect(result.current.selection.error).toBe('Сеть недоступна');

    const graph = deferred<GraphResponse>();
    vi.mocked(api.graph).mockReturnValueOnce(graph.promise);
    let request!: Promise<void>;
    act(() => { request = result.current.select({ kind: 'node', id: '0008' }); });
    expect(result.current.selection.node).toBeNull();
    await act(async () => { graph.resolve(graphResponse('0008')); await request; });
    expect(result.current.selection.node?.gid).toBe('0008');
  });

  it('does not expose the previous graph when another target fails to load', async () => {
    const { result } = await readyWorkspace();
    await act(async () => { await result.current.select({ kind: 'node', id: '0007' }); });
    vi.mocked(api.graph).mockRejectedValueOnce(new ApiError('Сеть недоступна', 0, 'NETWORK_ERROR'));
    await act(async () => { await result.current.select({ kind: 'node', id: '0008' }); });
    expect(result.current.selection.scope).toEqual({ kind: 'node', id: '0008' });
    expect(result.current.selection.node).toBeNull();
    expect(result.current.selection.cluster).toBeNull();
    expect(result.current.selection.graph).toBeNull();
    expect(result.current.selection.error).toBe('Сеть недоступна');
  });

  it('associates the source reported by health with the loaded run', async () => {
    vi.mocked(api.health).mockResolvedValueOnce({ ...healthResponse(), dataSource: 'fixtures' });
    const { result } = await readyWorkspace();
    expect(result.current.dataset?.dataSource).toBe('fixtures');
    vi.mocked(api.health).mockResolvedValueOnce({ ...healthResponse(NEXT_RUN), dataSource: 'artifacts' });
    vi.mocked(api.priorities).mockResolvedValueOnce({ contract_version: '1.0', run_id: NEXT_RUN, items: [], total: 0 });
    vi.mocked(api.clusters).mockResolvedValueOnce({ contract_version: '1.0', run_id: NEXT_RUN, items: [] });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.dataset?.runId).toBe(NEXT_RUN);
    expect(result.current.dataset?.dataSource).toBe('artifacts');
  });

  it('keeps B selected when a transport ignores abort and returns A late', async () => {
    const nodeA = deferred<NodeResponse>();
    const graphA = deferred<GraphResponse>();
    const nodeB = deferred<NodeResponse>();
    const graphB = deferred<GraphResponse>();
    // These promises deliberately do not react to AbortSignal.
    vi.mocked(api.node).mockImplementation((gid) => gid === 'A' ? nodeA.promise : nodeB.promise);
    vi.mocked(api.graph).mockImplementation((query) => query.gid === 'A' ? graphA.promise : graphB.promise);
    const { result } = await readyWorkspace();
    let firstRequest!: Promise<void>;
    let secondRequest!: Promise<void>;

    act(() => { firstRequest = result.current.select({ kind: 'node', id: 'A' }); });
    const firstSignal = vi.mocked(api.node).mock.calls[0][1];
    act(() => { secondRequest = result.current.select({ kind: 'node', id: 'B' }); });
    expect(firstSignal?.aborted).toBe(true);

    await act(async () => {
      nodeB.resolve(nodeResponse('B'));
      graphB.resolve(graphResponse('B'));
      await secondRequest;
    });
    expect(result.current.selection.node?.gid).toBe('B');
    expect(result.current.selection.graph?.nodes[0].gid).toBe('B');

    await act(async () => {
      nodeA.resolve(nodeResponse('A'));
      graphA.resolve(graphResponse('A'));
      await firstRequest;
    });
    expect(result.current.selection.scope).toEqual({ kind: 'node', id: 'B' });
    expect(result.current.selection.node?.gid).toBe('B');
    expect(result.current.selection.graph?.nodes[0].gid).toBe('B');
    expect(result.current.selection.loading).toBe(false);
    expect(result.current.selection.error).toBeNull();
  });

  it.each(['detail', 'graph'] as const)(
    'refreshes and clears old data when the %s response has a different run_id',
    async (changedResponse) => {
      const { result } = await readyWorkspace();
      await act(async () => { await result.current.select({ kind: 'node', id: 'old-node' }); });
      expect(result.current.selection.node?.gid).toBe('old-node');

      // Pause refresh to observe that old data disappears before the new run arrives.
      const nextHealth = deferred<HealthResult>();
      vi.mocked(api.health).mockReturnValueOnce(nextHealth.promise);
      vi.mocked(api.priorities).mockResolvedValue({
        contract_version: '1.0', run_id: NEXT_RUN, items: [makeNode('fresh-node')], total: 1,
      });
      vi.mocked(api.clusters).mockResolvedValue({
        contract_version: '1.0', run_id: NEXT_RUN, items: [],
      });
      vi.mocked(api.node).mockResolvedValueOnce(nodeResponse('requested-node', changedResponse === 'detail' ? NEXT_RUN : INITIAL_RUN));
      vi.mocked(api.graph).mockResolvedValueOnce(graphResponse('requested-node', changedResponse === 'graph' ? NEXT_RUN : INITIAL_RUN));

      await act(async () => { await result.current.select({ kind: 'node', id: 'requested-node' }); });
      expect(api.health).toHaveBeenCalledTimes(2);
      expect(result.current.dataset).toBeNull();
      expect(result.current.selection.scope).toBeNull();
      expect(result.current.selection.node).toBeNull();
      expect(result.current.selection.cluster).toBeNull();
      expect(result.current.selection.graph).toBeNull();
      expect(result.current.loading).toBe(true);
      expect(result.current.notice).toContain('Расчёт изменился');

      await act(async () => { nextHealth.resolve(healthResponse(NEXT_RUN)); });
      await waitFor(() => expect(result.current.dataset?.runId).toBe(NEXT_RUN));
      expect(result.current.dataset?.priorities.map((node) => node.gid)).toEqual(['fresh-node']);
      expect(result.current.selection.scope).toBeNull();
      expect(result.current.selection.node).toBeNull();
      expect(result.current.selection.graph).toBeNull();
      expect(result.current.loading).toBe(false);
    },
  );

  it('clears the previous card and graph when an exact gid lookup returns 404', async () => {
    const { result } = await readyWorkspace();
    await act(async () => { await result.current.select({ kind: 'node', id: 'existing-node' }); });
    expect(result.current.selection.node?.gid).toBe('existing-node');
    expect(result.current.selection.graph).not.toBeNull();

    vi.mocked(api.node).mockRejectedValueOnce(new ApiError('Узел не найден.', 404, 'NOT_FOUND', INITIAL_RUN));
    await act(async () => { await result.current.select({ kind: 'node', id: 'unknown-node' }); });

    expect(result.current.selection.node).toBeNull();
    expect(result.current.selection.cluster).toBeNull();
    expect(result.current.selection.graph).toBeNull();
    expect(result.current.selection.loading).toBe(false);
    expect(result.current.selection.error).toContain('«unknown-node» не найден');
    expect(result.current.dataset?.runId).toBe(INITIAL_RUN);
  });

  it('preserves the leading zeroes in gid and distinguishes unknown values from zero', async () => {
    const { result } = await readyWorkspace();
    await act(async () => { await result.current.select({ kind: 'node', id: '0007' }); });

    expect(api.node).toHaveBeenCalledWith('0007', expect.any(AbortSignal));
    expect(api.graph).toHaveBeenCalledWith({ gid: '0007', radius: 1, limit: 300 }, expect.any(AbortSignal));
    const node = result.current.selection.node!;
    expect(node.gid).toBe('0007');
    expect(formatNumber(node.metrics.out_in_ratio)).toBe('нет данных');
    expect(formatValue(node.quality.is_seed)).toBe('нет данных');
    expect(formatNumber(node.metrics.tx_in_count)).toBe('0');
    expect(formatNumber(node.role_score)).toBe('0');
    expect(formatValue(node.quality.hop_depth)).toBe('0');
    expect(formatMoney(node.metrics.in_amount_kzt)).toBe('0,00\u00a0₸');
  });
});

it('formats decimal money without rounding through a binary float', () => {
  expect(formatMoney('9007199254740993.0001')).toBe('9\u00a0007\u00a0199\u00a0254\u00a0740\u00a0993,0001\u00a0₸');
  expect(formatMoney(null)).toBe('нет данных');
});
