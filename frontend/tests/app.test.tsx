// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import { api } from '../src/api/client';
import type { AIResponse, GraphResponse, NodeRecord } from '../src/types/api';
import fixtureNodes from '../../tests/fixtures/contract-v1/nodes.json';

// Keep the real workspace and AI components; replace only the canvas renderer.
vi.mock('../src/components/GraphPanel', () => ({
  GraphPanel: ({ onRadiusChange, loading }: { onRadiusChange: (radius: 1 | 2) => void; loading: boolean }) => (
    <div>
      <button disabled={loading} onClick={() => onRadiusChange(1)}>Радиус 1</button>
      <button disabled={loading} onClick={() => onRadiusChange(2)}>Радиус 2</button>
    </div>
  ),
}));

const RUN = 'app-test-run-1';
const NEXT_RUN = 'app-test-run-2';
const nodes = fixtureNodes as NodeRecord[];
const answer: AIResponse = { contract_version: '1.0', run_id: RUN, status: 'ok', summary: 'Сохранённый ответ AI', claims: [], checks: [], limitations: [], fallback_reason: null };

function graphResponse(gid: string, runId = RUN): GraphResponse {
  return { contract_version: '1.0', run_id: runId, nodes: [nodes.find(node => node.gid === gid)!], edges: [], truncated: false, total_nodes: 1, shown_nodes: 1 };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(resolvePromise => { resolve = resolvePromise; });
  return { promise, resolve };
}

async function selectNode(gid: string) {
  fireEvent.change(screen.getByRole('textbox', { name: 'Найти узел' }), { target: { value: gid } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Найти узел' })); });
}

async function readyApp() {
  const view = render(<App />);
  await screen.findByText(RUN);
  await selectNode('0007');
  await screen.findByLabelText('Исследовать вопрос');
  return view;
}

beforeEach(() => {
  vi.stubEnv('VITE_DATA_SOURCE', '');
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', contract_version: '1.0', run_id: RUN, data_ready: true, dataSource: 'artifacts' });
  vi.spyOn(api, 'priorities').mockResolvedValue({ contract_version: '1.0', run_id: RUN, items: nodes, total: nodes.length });
  vi.spyOn(api, 'clusters').mockResolvedValue({ contract_version: '1.0', run_id: RUN, items: [] });
  vi.spyOn(api, 'node').mockImplementation(async gid => ({ contract_version: '1.0', run_id: RUN, node: nodes.find(node => node.gid === gid)! }));
  vi.spyOn(api, 'graph').mockImplementation(async query => graphResponse(query.gid!));
  vi.spyOn(api, 'explain').mockResolvedValue(answer);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('AI state while navigating the workspace', () => {
  it('preserves the draft and completed answer throughout a same-node radius refresh', async () => {
    await readyApp();
    const question = screen.getByLabelText('Исследовать вопрос') as HTMLTextAreaElement;
    fireEvent.change(question, { target: { value: 'Черновик вопроса' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Объяснить' })); });
    expect(screen.getByText(answer.summary)).toBeTruthy();
    const graph = deferred<GraphResponse>();
    vi.mocked(api.graph).mockReturnValueOnce(graph.promise);

    fireEvent.click(screen.getByRole('button', { name: 'Радиус 2' }));
    expect(screen.getByLabelText('Исследовать вопрос')).toBe(question);
    expect(question.value).toBe('Черновик вопроса');
    expect(screen.getByText(answer.summary)).toBeTruthy();
    await act(async () => { graph.resolve(graphResponse('0007')); });
    expect(screen.getByLabelText('Исследовать вопрос')).toBe(question);
    expect(screen.getByText(answer.summary)).toBeTruthy();
    expect(question.value).toBe('Черновик вопроса');

    fireEvent.click(screen.getByRole('button', { name: 'Радиус 2' }));
    expect(api.graph).toHaveBeenCalledTimes(2);
  });

  it('keeps an AI request running during a radius refresh and cancels it on a target change', async () => {
    await readyApp();
    const first = deferred<AIResponse>();
    vi.mocked(api.explain).mockReturnValueOnce(first.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Объяснить' }));
    const signal = vi.mocked(api.explain).mock.calls[0][2]!;
    const graph = deferred<GraphResponse>();
    vi.mocked(api.graph).mockReturnValueOnce(graph.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Радиус 2' }));
    expect(signal.aborted).toBe(false);
    await act(async () => { first.resolve(answer); });
    expect(screen.getByText(answer.summary)).toBeTruthy();
    await act(async () => { graph.resolve(graphResponse('0007')); });
    expect(screen.getByText(answer.summary)).toBeTruthy();

    const stale = deferred<AIResponse>();
    vi.mocked(api.explain).mockReturnValueOnce(stale.promise);
    fireEvent.change(screen.getByLabelText('Исследовать вопрос'), { target: { value: 'Старый вопрос' } });
    fireEvent.click(screen.getByRole('button', { name: 'Объяснить' }));
    const staleSignal = vi.mocked(api.explain).mock.calls[1][2]!;
    await selectNode('7');
    expect(staleSignal.aborted).toBe(true);
    expect((screen.getByLabelText('Исследовать вопрос') as HTMLTextAreaElement).value).toBe('');
    await act(async () => { stale.resolve({ ...answer, summary: 'Устаревший ответ' }); });
    expect(screen.queryByText('Устаревший ответ')).toBeNull();
  });

  it('clears retained AI state when a radius response belongs to a different run', async () => {
    await readyApp();
    fireEvent.change(screen.getByLabelText('Исследовать вопрос'), { target: { value: 'Вопрос старого запуска' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Объяснить' })); });
    vi.mocked(api.graph).mockResolvedValueOnce(graphResponse('0007', NEXT_RUN));
    vi.mocked(api.health).mockResolvedValue({ status: 'ok', contract_version: '1.0', run_id: NEXT_RUN, data_ready: true, dataSource: 'artifacts' });
    vi.mocked(api.priorities).mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: nodes, total: nodes.length });
    vi.mocked(api.clusters).mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: [] });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Радиус 2' })); });
    await screen.findByText(NEXT_RUN);
    expect(screen.queryByText(answer.summary)).toBeNull();
    expect(screen.queryByLabelText('Исследовать вопрос')).toBeNull();
    vi.mocked(api.node).mockResolvedValueOnce({ contract_version: '1.0', run_id: NEXT_RUN, node: nodes[0] });
    vi.mocked(api.graph).mockResolvedValueOnce(graphResponse('0007', NEXT_RUN));
    await selectNode('0007');
    expect((screen.getByLabelText('Исследовать вопрос') as HTMLTextAreaElement).value).toBe('');
  });
});

describe('dataset source notice', () => {
  it('uses the health header and updates the notice when the run source changes', async () => {
    vi.mocked(api.health).mockRestore();
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', contract_version: '1.0', run_id: RUN, data_ready: true }), { headers: { 'X-Data-Source': 'fixtures' } }));
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await screen.findByText(RUN);
    expect(screen.getByText('Искусственные тестовые данные')).toBeTruthy();
    expect(screen.getByText(/Данные искусственные и не являются/)).toBeTruthy();

    vi.stubEnv('VITE_DATA_SOURCE', 'fixture');
    fetch.mockResolvedValue(new Response(JSON.stringify({ status: 'ok', contract_version: '1.0', run_id: NEXT_RUN, data_ready: true }), { headers: { 'X-Data-Source': 'artifacts' } }));
    vi.mocked(api.priorities).mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: nodes, total: nodes.length });
    vi.mocked(api.clusters).mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Обновить набор данных' }));
    await screen.findByText(NEXT_RUN);
    expect(screen.queryByText(/Данные искусственные и не являются/)).toBeNull();
    expect(screen.getByText('Источник: результаты расчёта')).toBeTruthy();
  });

  it('uses the fixture environment fallback only when the source is unknown', async () => {
    vi.stubEnv('VITE_DATA_SOURCE', 'fixture');
    vi.mocked(api.health).mockResolvedValue({ status: 'ok', contract_version: '1.0', run_id: RUN, data_ready: true, dataSource: null });
    render(<App />);
    await waitFor(() => expect(screen.getByText(RUN)).toBeTruthy());
    expect(screen.getByText(/Данные искусственные и не являются/)).toBeTruthy();
  });
});
