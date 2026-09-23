// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AiPanel } from '../src/components/AiPanel';
import { api, ApiError } from '../src/api/client';
import type { AIResponse } from '../src/types/api';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function response(summary: string, run = 'run-1'): AIResponse {
  return { contract_version: '1.0', run_id: run, status: 'ok', summary, claims: [], checks: [], limitations: [], fallback_reason: null };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

describe('AI panel request ownership', () => {
  it('aborts target A and ignores its late response after B finishes', async () => {
    const first = deferred<AIResponse>();
    const second = deferred<AIResponse>();
    const explain = vi.spyOn(api, 'explain').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const onRunConflict = vi.fn();
    const view = render(createElement(AiPanel, { runId: 'run-1', target: { kind: 'node', id: '0007' }, onRunConflict }));
    fireEvent.click(screen.getByRole('button', { name: 'Объяснить' }));
    const oldSignal = explain.mock.calls[0][2]!;
    view.rerender(createElement(AiPanel, { runId: 'run-1', target: { kind: 'node', id: '0008' }, onRunConflict }));
    expect(oldSignal.aborted).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Объяснить' }));
    await act(async () => { second.resolve(response('Результат второго узла')); });
    expect(screen.getByText('Результат второго узла')).toBeTruthy();
    await act(async () => { first.resolve(response('Устаревший первый результат')); });
    expect(screen.queryByText('Устаревший первый результат')).toBeNull();
    expect(screen.getByText('Результат второго узла')).toBeTruthy();
    expect(onRunConflict).not.toHaveBeenCalled();
  });

  it('clears prior results and question when run changes', async () => {
    vi.spyOn(api, 'explain').mockResolvedValue(response('Старый запуск'));
    const onRunConflict = vi.fn();
    const view = render(createElement(AiPanel, { runId: 'run-1', target: { kind: 'node', id: '0007' }, onRunConflict }));
    fireEvent.change(screen.getByLabelText('Исследовать вопрос'), { target: { value: 'Старый вопрос' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Объяснить' })); });
    expect(screen.getByText('Старый запуск')).toBeTruthy();
    view.rerender(createElement(AiPanel, { runId: 'run-2', target: { kind: 'node', id: '0007' }, onRunConflict }));
    expect(screen.queryByText('Старый запуск')).toBeNull();
    expect((screen.getByLabelText('Исследовать вопрос') as HTMLTextAreaElement).value).toBe('');
  });

  it('reports 409 and requests an explicit data refresh', async () => {
    vi.spyOn(api, 'explain').mockRejectedValue(new ApiError('Stale run', 409, 'STALE_RUN', 'run-2'));
    const onRunConflict = vi.fn();
    render(createElement(AiPanel, { runId: 'run-1', target: { kind: 'node', id: '0007' }, onRunConflict }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Объяснить' })); });
    expect(onRunConflict).toHaveBeenCalledOnce();
    expect(screen.getByRole('alert').textContent).toContain('Обновите данные');
    expect(screen.getByRole('button', { name: 'Обновить данные' })).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Объяснить' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('retries investigation with the original question and clearly shows fallback evidence', async () => {
    const result: AIResponse = { ...response('Локальный результат'), status: 'fallback', fallback_reason: 'Ключ провайдера отсутствует', claims: [{ text: 'Проверен узел', evidence_ids: ['node:0007'] }], checks: [{ tool: 'get_node', args: { gid: '0007' }, result: { gid: '0007' }, evidence_id: 'node:0007' }], limitations: ['Неполный вход'] };
    const investigate = vi.spyOn(api, 'investigate').mockRejectedValueOnce(new ApiError('Сеть недоступна', 0, 'NETWORK_ERROR')).mockResolvedValueOnce(result);
    render(createElement(AiPanel, { runId: 'run-1', target: { kind: 'node', id: '0007' }, onRunConflict: vi.fn() }));
    fireEvent.change(screen.getByLabelText('Исследовать вопрос'), { target: { value: 'Кто получает переводы?' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Исследовать' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Повторить запрос' })); });
    expect(investigate).toHaveBeenCalledTimes(2);
    expect(investigate.mock.calls[1][2]).toBe('Кто получает переводы?');
    expect(screen.getByText('Локальная справка (fallback)')).toBeTruthy();
    expect(screen.getByText('Ключ провайдера отсутствует')).toBeTruthy();
    expect(screen.getByText('Неполный вход')).toBeTruthy();
    expect(screen.getAllByText('node:0007').length).toBe(2);
    expect(screen.getByText('Аргументы')).toBeTruthy();
    expect(screen.getByText('Результат')).toBeTruthy();
  });
});
