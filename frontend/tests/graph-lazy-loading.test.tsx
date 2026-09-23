// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GraphPanel } from '../src/components/GraphPanel';
import type { GraphResponse, NodeRecord } from '../src/types/api';
import fixtureNodes from '../../tests/fixtures/contract-v1/nodes.json';

const graphModule = vi.hoisted(() => {
  let finishImport!: () => void;
  const pendingImport = new Promise<void>((resolve) => { finishImport = resolve; });
  return { load: vi.fn(), create: vi.fn(), pendingImport, finishImport };
});

vi.mock('cytoscape', async () => {
  graphModule.load();
  await graphModule.pendingImport;
  return { default: graphModule.create };
});

afterEach(cleanup);

it('does not load the engine for empty data or create a canvas after a cancelled import', async () => {
  const nodes = fixtureNodes as NodeRecord[];
  const graph: GraphResponse = { contract_version: '1.0', run_id: 'test-run', nodes, edges: [], truncated: false, shown_nodes: nodes.length, total_nodes: nodes.length };
  const base = {
    graph: null as GraphResponse | null, selectedGid: null, loading: false, onSelectNode: vi.fn(),
    radius: 1 as const, onRadiusChange: vi.fn(), scopeLabel: 'Тестовая область', error: null, onRetry: vi.fn(),
  };
  const view = render(<GraphPanel {...base} />);
  view.rerender(<GraphPanel {...base} graph={{ ...graph, nodes: [], shown_nodes: 0, total_nodes: 0 }} />);
  expect(graphModule.load).not.toHaveBeenCalled();

  view.rerender(<GraphPanel {...base} graph={graph} />);
  expect(screen.getByText('Подготавливаем граф…')).toBeTruthy();
  await waitFor(() => expect(graphModule.load).toHaveBeenCalledOnce());
  view.rerender(<GraphPanel {...base} />);
  await act(async () => { graphModule.finishImport(); await graphModule.pendingImport; });
  expect(graphModule.create).not.toHaveBeenCalled();
  expect(screen.getByText('Каждое исследование начинается с узла')).toBeTruthy();
});
