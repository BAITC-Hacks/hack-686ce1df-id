// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DemoEditor, canonicalAmount, type EditorMode } from '../src/components/DemoEditor';
import { demoApi } from '../src/api/demo';
import { ApiError } from '../src/api/client';
import { demoInfo, INITIAL_RUN, NEXT_RUN } from './helpers';
import type { MutationResponse } from '../src/types/demo';

const receipt: MutationResponse = { contract_version: '1.0', run_id: NEXT_RUN, created_id: 'custom-000001', select_gid: 'custom-000001', node_count: 501, transfer_count: 6000 };
beforeEach(() => {
  vi.spyOn(demoApi, 'addNode').mockResolvedValue(receipt);
  vi.spyOn(demoApi, 'addTransfer').mockResolvedValue({ ...receipt, created_id: 'tx-new', select_gid: '0007', node_count: 500, transfer_count: 6001 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function editor(mode: EditorMode = 'node', saved = vi.fn().mockResolvedValue(true), conflict = vi.fn().mockResolvedValue(NEXT_RUN)) {
  const close = vi.fn();
  const view = render(<DemoEditor mode={mode} info={demoInfo()} onClose={close} onSaved={saved} onConflict={conflict} />);
  return { ...view, close, saved, conflict };
}
function fillName(value = 'Айдана Садыкова') { fireEvent.change(screen.getByLabelText(/Имя/), { target: { value } }); }
function choose(label: string, query: string, option: string) {
  fireEvent.change(screen.getByRole('combobox', { name: label }), { target: { value: query } });
  fireEvent.click(screen.getByRole('option', { name: option }));
}

describe('node editor', () => {
  it('validates required name then preserves Cyrillic, gid zeroes and explicit unknown quality', async () => {
    const view = editor();
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(screen.getByText('Введите имя от 1 до 120 символов без управляющих знаков.')).toBeTruthy();
    expect(demoApi.addNode).not.toHaveBeenCalled();
    fillName('  Айдана Садыкова  ');
    fireEvent.change(screen.getByLabelText(/ID узла/), { target: { value: '000007' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(view.close).toHaveBeenCalled());
    expect(demoApi.addNode).toHaveBeenCalledWith(expect.objectContaining({ display_name: 'Айдана Садыкова', gid: '000007', run_id: INITIAL_RUN, group: null, quality: { is_seed: null, outbound_censored: null, inbound_incomplete: null, hop_depth: null } }));
  });
  it('keeps the draft during conflict refresh and only resubmits after an explicit save', async () => {
    vi.mocked(demoApi.addNode).mockRejectedValueOnce(new ApiError('stale', 409, 'stale_run', NEXT_RUN));
    const view = editor();
    fillName('Черновик Имени');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await screen.findByRole('button', { name: 'Обновить, сохранив черновик' });
    expect((screen.getByLabelText(/Имя/) as HTMLInputElement).value).toBe('Черновик Имени');
    fireEvent.click(screen.getByRole('button', { name: 'Обновить, сохранив черновик' }));
    await screen.findByRole('button', { name: 'Сохранить' });
    expect(demoApi.addNode).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText(/Имя/) as HTMLInputElement).value).toBe('Черновик Имени');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(view.close).toHaveBeenCalled());
    const calls = vi.mocked(demoApi.addNode).mock.calls;
    expect(calls[1][0].run_id).toBe(NEXT_RUN);
    expect(calls[1][0].request_id).not.toBe(calls[0][0].request_id);
  });
  it('retains a lost-response command while hidden, even if reopening with another mode', async () => {
    vi.mocked(demoApi.addNode).mockRejectedValueOnce(new ApiError('network', 0, 'NETWORK_ERROR'));
    const saved = vi.fn().mockResolvedValue(true);
    function Harness() {
      const [mode, setMode] = useState<EditorMode>('node');
      return <><button onClick={() => setMode('transfer')}>Открыть перевод</button><DemoEditor mode={mode} info={demoInfo()} onClose={() => setMode(null)} onSaved={saved} onConflict={vi.fn()} /></>;
    }
    render(<Harness />);
    fillName('Сохранённый Черновик');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await screen.findByRole('button', { name: 'Повторить сохранение' });
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Открыть перевод' }));
    expect(screen.getByRole('heading', { name: 'Добавить узел' })).toBeTruthy();
    expect(screen.getByLabelText(/Имя/).matches(':disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Повторить сохранение' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(vi.mocked(demoApi.addNode).mock.calls[1][0]).toEqual(vi.mocked(demoApi.addNode).mock.calls[0][0]);
    expect(demoApi.addTransfer).not.toHaveBeenCalled();
  });
  it('focuses the form, handles Escape and restores the trigger focus', async () => {
    function Harness() {
      const [mode, setMode] = useState<EditorMode>(null);
      return <><button onClick={() => setMode('node')}>Открыть</button><DemoEditor mode={mode} info={demoInfo()} onClose={() => setMode(null)} onSaved={vi.fn()} onConflict={vi.fn()} /></>;
    }
    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Открыть' });
    trigger.focus(); fireEvent.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(/Имя/)));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });
  it('offers only refresh after confirmed success with failed read', async () => {
    editor('node', vi.fn().mockResolvedValue(false)); fillName();
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await screen.findByRole('button', { name: 'Обновить данные' });
    fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }));
    await waitFor(() => expect(demoApi.addNode).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
  });
});

describe('transfer editor', () => {
  it('selects a namesake by gid and a node outside the top, sends exact comma money', async () => {
    const view = editor('transfer');
    choose('Отправитель', 'Иванов', 'Александр Иванов · 0007');
    choose('Получатель', 'Садыкова', 'Айдана Садыкова · outside-top');
    fireEvent.change(screen.getByLabelText('Сумма, ₸'), { target: { value: '12500,50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(view.close).toHaveBeenCalled());
    expect(demoApi.addTransfer).toHaveBeenCalledWith(expect.objectContaining({ source: '0007', target: 'outside-top', amount_kzt: '12500.50', date: '2026-07-15' }));
  });
  it('rejects self transfers and preserves entered data', async () => {
    editor('transfer');
    choose('Отправитель', '7', 'Александр Иванов · 0007');
    choose('Получатель', '7', 'Александр Иванов · 0007');
    fireEvent.change(screen.getByLabelText('Сумма, ₸'), { target: { value: '5000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(screen.getByText('Отправитель и получатель должны различаться.')).toBeTruthy();
    expect(demoApi.addTransfer).not.toHaveBeenCalled();
    expect((screen.getByLabelText('Сумма, ₸') as HTMLInputElement).value).toBe('5000');
  });
});
it('canonicalizes bounded money without float arithmetic', () => {
  expect(canonicalAmount('0005000,01')).toBe('5000.01');
  expect(canonicalAmount('1000000000000')).toBe('1000000000000.00');
  for (const invalid of ['4999.99', '1000000000000.01', '1e6', 'NaN', '5000.001', '-6000']) expect(canonicalAmount(invalid)).toBeNull();
});
