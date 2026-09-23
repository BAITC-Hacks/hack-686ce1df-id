// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GraphPanel } from '../src/components/GraphPanel';
import type { NodeRecord } from '../src/types/api';
import fixtureNodes from '../../tests/fixtures/contract-v1/nodes.json';

vi.mock('cytoscape', () => { throw new Error('Simulated graph chunk download failure'); });

afterEach(cleanup);

it('keeps accessible graph data and retry available when the graph module cannot load', async () => {
  const onSelectNode = vi.fn();
  const nodes = fixtureNodes as NodeRecord[];
  render(<GraphPanel graph={{ contract_version: '1.0', run_id: 'test-run', nodes,
    edges: [{ source: '0007', target: '7', amount_kzt: '5.00', tx_count: 1 }],
    truncated: false, shown_nodes: nodes.length, total_nodes: nodes.length,
  }} selectedGid={null} loading={false} onSelectNode={onSelectNode} radius={1} onRadiusChange={vi.fn()}
    scopeLabel="Тестовая область" error={null} onRetry={vi.fn()} />);

  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Не удалось отобразить граф'));
  expect(screen.getByRole('button', { name: 'Повторить отображение' })).toHaveProperty('disabled', false);
  fireEvent.click(screen.getByText('Связи в текстовом виде'));
  fireEvent.click(screen.getByRole('button', { name: 'Открыть отправителя 0007' }));
  expect(onSelectNode).toHaveBeenCalledWith('0007');
});
