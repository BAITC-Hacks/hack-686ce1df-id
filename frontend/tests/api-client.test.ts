import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../src/api/client';

const envelope = { contract_version: '1.0', run_id: 'review-run' };

afterEach(() => vi.unstubAllGlobals());

describe('data source metadata', () => {
  it.each(['fixtures', 'artifacts', 'unavailable'] as const)('reads %s from the current health response header', async (source) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...envelope, status: 'ok', data_ready: source !== 'unavailable',
      run_id: source === 'unavailable' ? null : envelope.run_id,
    }), { headers: { 'X-Data-Source': source } })));

    expect((await api.health()).dataSource).toBe(source);
  });

  it('does not reuse a previous source when a later response omits the header', async () => {
    const body = JSON.stringify({ ...envelope, status: 'ok', data_ready: true });
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(body, { headers: { 'X-Data-Source': 'fixtures' } }))
      .mockResolvedValueOnce(new Response(body)));

    expect((await api.health()).dataSource).toBe('fixtures');
    expect((await api.health()).dataSource).toBeNull();
  });
});

describe('opaque identifier lookups', () => {
  it.each(['0007', 'review/0007', '.', '..', 'узел /?x=1&y=2+#%'])('preserves %j in node and cluster query parameters', async (id) => {
    const transport = vi.fn().mockImplementation(async () => new Response(JSON.stringify(envelope)));
    vi.stubGlobal('fetch', transport);

    await api.node(id);
    await api.cluster(id);

    const nodeUrl = new URL(transport.mock.calls[0][0], 'http://localhost');
    const clusterUrl = new URL(transport.mock.calls[1][0], 'http://localhost');
    expect(nodeUrl.pathname).toBe('/api/node');
    expect(nodeUrl.searchParams.get('gid')).toBe(id);
    expect(clusterUrl.pathname).toBe('/api/cluster');
    expect(clusterUrl.searchParams.get('cluster_id')).toBe(id);
    expect([...nodeUrl.searchParams.keys()]).toEqual(['gid']);
    expect([...clusterUrl.searchParams.keys()]).toEqual(['cluster_id']);
  });
});
