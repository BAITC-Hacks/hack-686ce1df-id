// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import { api } from '../src/api/client';
import { disabledDemo } from '../src/types/demo';
import type { DataSource, HealthResponse } from '../src/types/api';

// Graph rendering is independent of the origin banner and requires a real canvas.
vi.mock('../src/components/GraphPanel', () => ({ GraphPanel: () => null }));

const runId = 'data-source-test-run';
const fixtureNotice = 'Режим проверки интерфейса. Данные искусственные и не являются результатами анализа кейса.';
const health: HealthResponse = { status: 'ok', contract_version: '1.0', run_id: runId, data_ready: true };

function serveApi(initialSource: DataSource | null) {
  let source = initialSource;
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const path = input.split('?')[0];
    const headers = new Headers({ 'Content-Type': 'application/json' });
    // Only health declares the source; list responses remain contract JSON.
    if (path === '/api/health' && source !== null) headers.set('X-Data-Source', source);
    const data = path === '/api/health' ? health
      : path === '/api/priorities' ? { contract_version: '1.0', run_id: runId, items: [], total: 0 }
        : path === '/api/demo' ? disabledDemo(runId)
        : path === '/api/clusters' ? { contract_version: '1.0', run_id: runId, items: [] }
          : null;
    if (data === null) throw new Error(`Unexpected request: ${input}`);
    return new Response(JSON.stringify(data), { headers });
  }));
  return (nextSource: DataSource | null) => { source = nextSource; };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('runtime data source for a reusable frontend build', () => {
  it.each([
    { source: 'fixtures', buildSource: undefined, showsFixture: true },
    { source: 'fixtures', buildSource: 'artifacts', showsFixture: true },
    { source: 'artifacts', buildSource: 'fixture', showsFixture: false },
    { source: null, buildSource: 'fixture', showsFixture: true },
    { source: null, buildSource: undefined, showsFixture: false },
  ] as const)('uses header $source with build fallback $buildSource', async ({ source, buildSource, showsFixture }) => {
    vi.stubEnv('VITE_DATA_SOURCE', buildSource);
    serveApi(source);
    render(<App />);
    await screen.findByText('Расчёт загружен');

    expect(screen.queryByText(fixtureNotice) !== null).toBe(showsFixture);
    expect(screen.queryByText('Искусственные тестовые данные') !== null).toBe(showsFixture);
    if (!showsFixture) expect(screen.getByText(source === 'artifacts' ? 'Источник: результаты расчёта' : 'Источник данных не указан')).toBeTruthy();
  });

  it('refreshes the source and removes the previous fixture warning', async () => {
    vi.stubEnv('VITE_DATA_SOURCE', undefined);
    const setSource = serveApi('fixtures');
    render(<App />);
    await screen.findByText(fixtureNotice);

    setSource('artifacts');
    fireEvent.click(screen.getByRole('button', { name: 'Обновить набор данных' }));
    await waitFor(() => expect(screen.queryByText(fixtureNotice)).toBeNull());
    await screen.findByText('Расчёт загружен');
    expect(screen.getByText('Источник: результаты расчёта')).toBeTruthy();

    setSource('fixtures');
    fireEvent.click(screen.getByRole('button', { name: 'Обновить набор данных' }));
    await screen.findByText(fixtureNotice);
  });

  it('adds client metadata without requiring extra health JSON fields', async () => {
    serveApi('unavailable');
    expect(await api.health()).toEqual({ ...health, dataSource: 'unavailable' });
  });
});
