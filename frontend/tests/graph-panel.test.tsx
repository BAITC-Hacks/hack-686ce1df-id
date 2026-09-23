// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphPanel } from '../src/components/GraphPanel';
import type { EdgeRecord, GraphResponse, NodeRecord } from '../src/types/api';
import fixtureNodes from '../../tests/fixtures/contract-v1/nodes.json';

const graphModule = vi.hoisted(() => ({ create: vi.fn() }));

vi.mock('cytoscape', () => ({ default: graphModule.create }));

function renderer() {
  const edges = { removeClass: vi.fn(), addClass: vi.fn() };
  const node = { data: vi.fn(), addClass: vi.fn(), connectedEdges: () => edges };
  return {
    on: vi.fn(), off: vi.fn(), destroy: vi.fn(), resize: vi.fn(),
    batch: (action: () => void) => action(),
    elements: () => ({ remove: vi.fn() }),
    nodes: () => ({ empty: () => false, removeClass: vi.fn() }),
    edges: () => edges,
    getElementById: () => node,
    add: vi.fn(), fit: vi.fn(), center: vi.fn(), zoom: vi.fn(() => 1),
    minZoom: () => 0.08, maxZoom: () => 3, width: () => 500, height: () => 300,
  };
}

const nodes = fixtureNodes as NodeRecord[];
const edges: EdgeRecord[] = [
  { source: '0007', target: '7', amount_kzt: '1234.50', tx_count: 3 },
  { source: '7', target: '0007', amount_kzt: '90.00', tx_count: 1 },
];

function graph(graphEdges = edges): GraphResponse {
  return { contract_version: '1.0', run_id: 'test-run', nodes, edges: graphEdges, truncated: false, shown_nodes: nodes.length, total_nodes: nodes.length };
}

function props() {
  return {
    graph: null as GraphResponse | null, selectedGid: null, loading: false, onSelectNode: vi.fn(),
    radius: 1 as const, onRadiusChange: vi.fn(), scopeLabel: 'Тестовая область', error: null, onRetry: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  graphModule.create.mockImplementation(renderer);
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn();
    disconnect = vi.fn();
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('graph loading and accessible data', () => {
  it('uses the current graph after loading and destroys the canvas when data clears', async () => {
    const base = props();
    const view = render(<GraphPanel {...base} graph={graph()} />);
    const latest = { ...graph([]), nodes: [nodes[0]], shown_nodes: 1, total_nodes: 1 };
    view.rerender(<GraphPanel {...base} graph={latest} />);
    await waitFor(() => expect(graphModule.create).toHaveBeenCalledOnce());
    const cy = graphModule.create.mock.results[0].value as ReturnType<typeof renderer>;
    await waitFor(() => expect(cy.add).toHaveBeenLastCalledWith([
      expect.objectContaining({ data: expect.objectContaining({ gid: nodes[0].gid }) }),
    ]));
    fireEvent.click(screen.getByRole('button', { name: 'Увеличить граф' }));
    expect(cy.zoom).toHaveBeenLastCalledWith({ level: 1.25, renderedPosition: { x: 250, y: 150 } });
    view.rerender(<GraphPanel {...base} />);
    expect(cy.destroy).toHaveBeenCalledOnce();
  });

  it('exposes both edge directions with exact identifiers, amounts and selectable endpoints', async () => {
    const base = props();
    render(<GraphPanel {...base} graph={graph()} />);
    fireEvent.click(screen.getByText('Связи в текстовом виде'));
    const table = screen.getByRole('table', { name: 'Направленные связи показанной области' });
    const rows = within(table).getAllByRole('row');
    expect(within(rows[1]).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['0007', '7', '1\u00a0234,50\u00a0₸', '3']);
    expect(within(rows[2]).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['7', '0007', '90,00\u00a0₸', '1']);
    fireEvent.click(within(rows[2]).getByRole('button', { name: 'Открыть получателя 0007' }));
    expect(base.onSelectNode).toHaveBeenCalledWith('0007');
    await waitFor(() => expect(graphModule.create).toHaveBeenCalled());
  });

  it('pages the edge alternative without mounting an unbounded table', async () => {
    const manyEdges = Array.from({ length: 31 }, (_, index) => ({ source: `source-${index}`, target: `target-${index}`, amount_kzt: '1.00', tx_count: 1 }));
    render(<GraphPanel {...props()} graph={graph(manyEdges)} />);
    fireEvent.click(screen.getByText('Связи в текстовом виде'));
    const table = screen.getByRole('table', { name: 'Направленные связи показанной области' });
    expect(within(table).getAllByRole('row')).toHaveLength(26);
    expect(screen.queryByRole('button', { name: 'Открыть отправителя source-25' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
    expect(within(table).getAllByRole('row')).toHaveLength(7);
    expect(screen.getByText('Связи 26–31 из 31')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Открыть отправителя source-25' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(screen.getByRole('button', { name: 'Открыть отправителя source-0' })).toBeTruthy();
    await waitFor(() => expect(graphModule.create).toHaveBeenCalled());
  });
});
