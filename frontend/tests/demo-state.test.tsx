// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../src/api/client';
import { demoApi } from '../src/api/demo';
import { useWorkspace } from '../src/state/useWorkspace';
import { useDemoMutation } from '../src/state/useDemoMutation';
import { deferred, demoInfo, graphResponse, healthResponse, makeNode, nodeResponse, INITIAL_RUN, NEXT_RUN } from './helpers';
import type { DemoInfo, MutationResponse } from '../src/types/demo';

const receipt: MutationResponse = { contract_version: '1.0', run_id: NEXT_RUN, created_id: 'custom-000001', select_gid: 'custom-000001', node_count: 501, transfer_count: 6000 };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function workspaceMocks(run = INITIAL_RUN) {
  vi.spyOn(api, 'health').mockResolvedValue(healthResponse(run));
  vi.spyOn(demoApi, 'info').mockResolvedValue(demoInfo(run));
  vi.spyOn(api, 'priorities').mockResolvedValue({ contract_version: '1.0', run_id: run, items: [makeNode('0007')], total: 500 });
  vi.spyOn(api, 'clusters').mockResolvedValue({ contract_version: '1.0', run_id: run, items: [] });
  vi.spyOn(api, 'node').mockImplementation(async gid => nodeResponse(gid, run));
  vi.spyOn(api, 'graph').mockImplementation(async query => graphResponse(query.gid!, run));
}

describe('versioned demo metadata', () => {
  it('refreshes the latest version before selecting a saved node', async () => {
    workspaceMocks(NEXT_RUN);
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.dataset?.runId).toBe(NEXT_RUN));
    let success = false;
    await act(async () => { success = await result.current.refreshAndSelect('custom-000001'); });
    expect(success).toBe(true);
    expect(result.current.selection.node?.gid).toBe('custom-000001');
  });
  it('rejects names from a different run instead of mixing snapshots', async () => {
    workspaceMocks();
    vi.mocked(demoApi.info).mockResolvedValue(demoInfo(NEXT_RUN));
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.dataset).toBeNull();
    expect(result.current.error).toContain('изменился расчёт');
  });
  it('ignores a late old names response after refresh', async () => {
    workspaceMocks();
    const old = deferred<DemoInfo>();
    vi.mocked(demoApi.info).mockReturnValueOnce(old.promise);
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(demoApi.info).toHaveBeenCalledTimes(1));
    vi.mocked(api.health).mockResolvedValue(healthResponse(NEXT_RUN));
    vi.mocked(api.priorities).mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: [], total: 500 });
    vi.mocked(api.clusters).mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: [] });
    vi.mocked(demoApi.info).mockResolvedValue(demoInfo(NEXT_RUN));
    await act(async () => { await result.current.refresh(); });
    await act(async () => { old.resolve(demoInfo(INITIAL_RUN)); });
    expect(result.current.dataset?.demo.run_id).toBe(NEXT_RUN);
  });
  it('allows old read-only backends with a missing demo route', async () => {
    workspaceMocks();
    vi.mocked(demoApi.info).mockRejectedValue(new ApiError('Missing', 404, 'not_found'));
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.dataset?.runId).toBe(INITIAL_RUN));
    expect(result.current.dataset?.demo.enabled).toBe(false);
    expect(result.current.dataset?.demo.nodes).toEqual([]);
  });
  it('reports other capability load failures', async () => {
    workspaceMocks();
    vi.mocked(demoApi.info).mockRejectedValue(new ApiError('Demo unavailable', 503, 'unavailable'));
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.error).toBe('Demo unavailable'));
    expect(result.current.dataset).toBeNull();
  });
});

describe('mutation outcome handling', () => {
  it('retries exactly the same body and UUID after a lost response', async () => {
    const post = vi.spyOn(demoApi, 'addNode').mockRejectedValueOnce(new ApiError('Lost', 0, 'NETWORK_ERROR')).mockResolvedValueOnce(receipt);
    const saved = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useDemoMutation(saved));
    await act(async () => { await result.current.submit('node', { display_name: 'Айдана Садыкова', gid: '0007' }, INITIAL_RUN); });
    expect(result.current.uncertain).toBe(true);
    await act(async () => { await result.current.submit('node', { display_name: 'Changed' }, NEXT_RUN); });
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.retry(); });
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][0]).toEqual(post.mock.calls[0][0]);
    expect(saved).toHaveBeenCalledWith('custom-000001');
  });
  it('requires explicit conflict acknowledgement and a new UUID after stale_run', async () => {
    const post = vi.spyOn(demoApi, 'addNode').mockRejectedValueOnce(new ApiError('Stale', 409, 'stale_run', NEXT_RUN)).mockResolvedValueOnce(receipt);
    const { result } = renderHook(() => useDemoMutation(vi.fn().mockResolvedValue(true)));
    const draft = { display_name: 'Черновик', gid: '007' };
    await act(async () => { await result.current.submit('node', draft, INITIAL_RUN); });
    expect(result.current.conflict).toBe(true);
    await act(async () => { await result.current.submit('node', draft, NEXT_RUN); });
    expect(post).toHaveBeenCalledTimes(1);
    act(() => result.current.acknowledgeConflict());
    await act(async () => { await result.current.submit('node', draft, NEXT_RUN); });
    expect(post.mock.calls[1][0].run_id).toBe(NEXT_RUN);
    expect(post.mock.calls[1][0].request_id).not.toBe(post.mock.calls[0][0].request_id);
    expect(post.mock.calls[1][0].display_name).toBe('Черновик');
  });
  it('never repeats a confirmed write when refresh failed', async () => {
    const post = vi.spyOn(demoApi, 'addNode').mockResolvedValue(receipt);
    const saved = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { result } = renderHook(() => useDemoMutation(saved));
    await act(async () => { await result.current.submit('node', { display_name: 'Имя' }, INITIAL_RUN); });
    expect(result.current.saved).toEqual(receipt);
    await act(async () => { await result.current.submit('node', { display_name: 'Имя' }, NEXT_RUN); await result.current.retry(); await result.current.retryRefresh(); });
    expect(post).toHaveBeenCalledTimes(1);
    expect(saved).toHaveBeenCalledTimes(2);
  });
  it('blocks double clicks synchronously before React rerenders', async () => {
    const request = deferred<MutationResponse>();
    const post = vi.spyOn(demoApi, 'addNode').mockReturnValue(request.promise);
    const { result } = renderHook(() => useDemoMutation(vi.fn().mockResolvedValue(true)));
    let pending!: Promise<boolean>;
    act(() => { pending = result.current.submit('node', { display_name: 'Имя' }, INITIAL_RUN); void result.current.submit('node', { display_name: 'Имя' }, INITIAL_RUN); });
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => { request.resolve(receipt); await pending; });
  });
  it('does not treat a duplicate gid as stale_run', async () => {
    vi.spyOn(demoApi, 'addNode').mockRejectedValue(new ApiError('Duplicate', 409, 'duplicate_node', INITIAL_RUN));
    const { result } = renderHook(() => useDemoMutation(vi.fn().mockResolvedValue(true)));
    await act(async () => { await result.current.submit('node', { display_name: 'Имя', gid: '0007' }, INITIAL_RUN); });
    expect(result.current.conflict).toBe(false);
    expect(result.current.uncertain).toBe(false);
  });
});
