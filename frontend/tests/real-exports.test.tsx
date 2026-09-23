// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import { api } from '../src/api/client';
import { ExportMenu } from '../src/components/ExportMenu';
import { disabledDemo } from '../src/types/demo';
import type { DataSource } from '../src/types/api';

vi.mock('../src/components/GraphPanel', () => ({ GraphPanel: () => null }));

const runId = 'real-export-test';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('submission CSV downloads', () => {
  it.each([
    ['nodes_roles', 'nodes_roles.csv', 'Роли узлов · CSV задания'],
    ['clusters_csv', 'clusters.csv', 'Кластеры · CSV задания'],
    ['top_nodes', 'top_nodes.csv', 'Приоритетные узлы · CSV задания'],
  ])('downloads %s using the submission filename %s', async (alias, filename, label) => {
    const download = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      download(this.download, this.href);
    });
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:test-csv');
      static revokeObjectURL = vi.fn();
    });
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', contract_version: '1.0', run_id: runId, data_ready: true, dataSource: 'artifacts' });
    const fetchFile = vi.fn().mockResolvedValue(new Response('gid,role\n0007,peripheral\n'));
    vi.stubGlobal('fetch', fetchFile);
    const onRunConflict = vi.fn();
    render(<ExportMenu realRun runId={runId} onRunConflict={onRunConflict} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выгрузки' }));
    expect(screen.getByText(label)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: `Скачать ${filename}` }));

    await waitFor(() => expect(download).toHaveBeenCalledWith(filename, 'blob:test-csv'));
    expect(fetchFile).toHaveBeenCalledWith(`/api/exports/${alias}`, expect.objectContaining({ cache: 'no-store' }));
    expect(api.health).toHaveBeenCalledTimes(2);
    expect(onRunConflict).not.toHaveBeenCalled();
  });

  it.each([false, true])('preserves existing exports for demo=%s', (demo) => {
    render(<ExportMenu demo={demo} runId="demo-test" onRunConflict={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выгрузки' }));
    for (const filename of ['nodes.json', 'edges.json', 'clusters.json', 'priorities.csv', 'roles.csv', 'rules.json', 'audit.json', 'manifest.json']) {
      expect(screen.getByRole('button', { name: `Скачать ${filename}` })).toBeTruthy();
    }
    expect(screen.queryByRole('button', { name: 'Скачать nodes_roles.csv' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Скачать clusters.csv' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Скачать top_nodes.csv' })).toBeNull();
    for (const filename of ['node_names.json', 'source.json', 'transfers.json']) {
      expect(screen.queryByRole('button', { name: `Скачать ${filename}` }) !== null).toBe(demo);
    }
  });
});

describe('real data source in the application', () => {
  it.each([
    { source: 'artifacts', id: runId, real: true },
    { source: 'fixtures', id: runId, real: false },
    { source: 'artifacts', id: 'other-completed-run', real: false },
  ] satisfies { source: DataSource; id: string; real: boolean }[])('identifies source=$source run=$id', async ({ source, id, real }) => {
    vi.stubEnv('VITE_DATA_SOURCE', undefined);
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      const path = input.split('?')[0];
      const headers = new Headers({ 'Content-Type': 'application/json' });
      if (path === '/api/health') headers.set('X-Data-Source', source);
      const data = path === '/api/health' ? { status: 'ok', contract_version: '1.0', run_id: id, data_ready: true }
        : path === '/api/priorities' ? { contract_version: '1.0', run_id: id, items: [], total: 0 }
          : path === '/api/demo' ? disabledDemo(id)
            : path === '/api/clusters' ? { contract_version: '1.0', run_id: id, items: [] }
              : null;
      if (data === null) throw new Error(`Unexpected request: ${input}`);
      return new Response(JSON.stringify(data), { headers });
    }));

    render(<App />);
    await screen.findByText('Расчёт загружен');
    expect(screen.queryByText('Источник: предоставленные данные задания') !== null).toBe(real);
    expect(screen.queryByRole('button', { name: 'Добавить узел' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Добавить перевод' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Выгрузки' }));
    expect(screen.queryByRole('button', { name: 'Скачать nodes_roles.csv' }) !== null).toBe(real);
  });
});
