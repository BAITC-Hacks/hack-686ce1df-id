import { useEffect, useId, useRef, useState } from 'react';
import type { Core, ElementDefinition, EventObject } from 'cytoscape';
import { ArrowRight, Expand, LoaderCircle, Minus, Network, Plus, RotateCcw } from 'lucide-react';
import type { GraphResponse } from '../types/api';
import { ROLE_COLORS, ROLE_LABELS, formatMoney } from '../format';

interface GraphPanelProps {
  graph: GraphResponse | null;
  selectedGid: string | null;
  loading: boolean;
  onSelectNode: (gid: string) => void;
  radius: 1 | 2;
  onRadiusChange: (radius: 1 | 2) => void;
  scopeLabel: string;
  error: string | null;
  onRetry: () => void;
}

const roles = ['consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral'] as const;
const nodeId = (gid: string) => `node:${gid}`;
const edgeId = (source: string, target: string) => `edge:${JSON.stringify([source, target])}`;
const compareIds = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const EDGE_PAGE_SIZE = 25;

function GraphEdges({ graph, disabled, onSelectNode }: {
  graph: GraphResponse;
  disabled: boolean;
  onSelectNode: (gid: string) => void;
}) {
  const [page, setPage] = useState(0);
  const lastPage = Math.max(0, Math.ceil(graph.edges.length / EDGE_PAGE_SIZE) - 1);
  const currentPage = Math.min(page, lastPage);
  const start = currentPage * EDGE_PAGE_SIZE;
  const edges = graph.edges.slice(start, start + EDGE_PAGE_SIZE);

  return <details className="graph-edge-list">
    <summary>Связи в текстовом виде</summary>
    {edges.length === 0 ? <p className="empty-note">В показанной области нет связей.</p> : <>
      <div className="graph-edge-table-wrap">
        <table className="graph-edge-table">
          <caption>Направленные связи показанной области</caption>
          <thead><tr><th scope="col">Отправитель</th><th scope="col">Получатель</th><th scope="col">Сумма</th><th scope="col">Переводов</th></tr></thead>
          <tbody>{edges.map((edge) => <tr key={edgeId(edge.source, edge.target)}>
            <td><button type="button" className="text-button" disabled={disabled} aria-label={`Открыть отправителя ${edge.source}`} onClick={() => onSelectNode(edge.source)}>{edge.source}</button></td>
            <td><button type="button" className="text-button" disabled={disabled} aria-label={`Открыть получателя ${edge.target}`} onClick={() => onSelectNode(edge.target)}>{edge.target}</button></td>
            <td>{formatMoney(edge.amount_kzt)}</td><td>{edge.tx_count.toLocaleString('ru-RU')}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="graph-edge-pagination" role="group" aria-label="Страницы списка связей">
        <button type="button" className="button button-secondary" disabled={disabled || currentPage === 0} onClick={() => setPage(currentPage - 1)}>Назад</button>
        <p aria-live="polite">Связи {start + 1}–{start + edges.length} из {graph.edges.length}</p>
        <button type="button" className="button button-secondary" disabled={disabled || currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Далее</button>
      </div>
    </>}
  </details>;
}

/** A finite, deterministic layout: 100 small force steps, with no running simulation. */
function positionsFor(graph: GraphResponse): Map<string, { x: number; y: number }> {
  const gids = graph.nodes.map((node) => node.gid).sort(compareIds);
  const indices = new Map(gids.map((gid, index) => [gid, index]));
  const positions = gids.map((_, index) => {
    const angle = index * 2.399963229728653;
    const distance = 40 * Math.sqrt(index);
    return { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance };
  });
  const edges = [...graph.edges]
    .sort((a, b) => compareIds(a.source, b.source) || compareIds(a.target, b.target))
    .map((edge) => [indices.get(edge.source), indices.get(edge.target)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] !== undefined && pair[1] !== undefined);

  for (let step = 0; step < 100; step += 1) {
    const forces = positions.map(({ x, y }) => ({ x: -x * 0.006, y: -y * 0.006 }));
    for (let i = 0; i < positions.length; i += 1) {
      for (let j = i + 1; j < positions.length; j += 1) {
        const dx = positions[i].x - positions[j].x;
        const dy = positions[i].y - positions[j].y;
        const squaredDistance = Math.max(dx * dx + dy * dy, 100);
        const strength = 1600 / squaredDistance;
        forces[i].x += dx * strength;
        forces[i].y += dy * strength;
        forces[j].x -= dx * strength;
        forces[j].y -= dy * strength;
      }
    }
    for (const [source, target] of edges) {
      if (source === target) continue;
      const dx = positions[target].x - positions[source].x;
      const dy = positions[target].y - positions[source].y;
      const distance = Math.max(Math.hypot(dx, dy), 1);
      const strength = (distance - 110) * 0.04 / distance;
      forces[source].x += dx * strength;
      forces[source].y += dy * strength;
      forces[target].x -= dx * strength;
      forces[target].y -= dy * strength;
    }
    const maximumStep = 12 * (1 - step / 100) + 0.4;
    for (let i = 0; i < positions.length; i += 1) {
      const length = Math.max(Math.hypot(forces[i].x, forces[i].y), 0.01);
      const scale = Math.min(maximumStep / length, 1);
      positions[i].x += forces[i].x * scale;
      positions[i].y += forces[i].y * scale;
    }
  }
  return new Map(gids.map((gid, index) => [nodeId(gid), positions[index]]));
}

function fitGraph(cy: Core) {
  if (cy.nodes().empty()) return;
  cy.fit(cy.elements(), 56);
  // A single isolated node should remain a node, rather than fill the canvas.
  if (cy.zoom() > 1.4) {
    cy.zoom(1.4);
    cy.center();
  }
}

export function GraphPanel({ graph, selectedGid, loading, onSelectNode, radius, onRadiusChange, scopeLabel, error, onRetry }: GraphPanelProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const topologyRef = useRef<string | null>(null);
  const onSelectRef = useRef(onSelectNode);
  const disabledRef = useRef(loading || Boolean(error));
  const [renderer, setRenderer] = useState<Core | null>(null);
  const [rendererError, setRendererError] = useState(false);
  const [rendererAttempt, setRendererAttempt] = useState(0);
  const selectorId = useId();
  const headingId = useId();
  const hasNodes = Boolean(graph?.nodes.length);
  const rendererLoading = hasNodes && !renderer && !rendererError;
  const unavailable = loading || Boolean(error) || !hasNodes || !renderer;

  useEffect(() => {
    onSelectRef.current = onSelectNode;
    disabledRef.current = loading || Boolean(error);
  }, [onSelectNode, loading, error]);

  useEffect(() => {
    const container = canvasRef.current;
    if (!container || !hasNodes) return;
    let disposed = false;
    let disposeRenderer: (() => void) | undefined;
    setRendererError(false);
    // Keep the graph engine out of the initial request; an empty workspace needs no canvas renderer.
    void import('cytoscape').then(({ default: cytoscape }) => {
      if (disposed) return;
      const cy = cytoscape({
        container,
        elements: [],
        layout: { name: 'preset' },
        minZoom: 0.08,
        maxZoom: 3,
        autounselectify: true,
        boxSelectionEnabled: false,
        style: [
          {
            selector: 'node',
            style: {
              width: 23,
              height: 23,
              'background-color': 'data(color)',
              'border-width': 3,
              'border-color': '#ffffff',
              label: '',
              color: '#243655',
              'font-size': 12,
              'font-family': 'Arial, sans-serif',
              'font-weight': 600,
              'text-valign': 'bottom',
              'text-margin-y': 10,
              'text-background-color': '#ffffff',
              'text-background-opacity': 0.95,
              'text-background-padding': '4px',
              'overlay-opacity': 0,
            },
          },
          {
            selector: 'edge',
            style: {
              width: 1.5,
              'line-color': '#bdcbdc',
              'target-arrow-color': '#9aadc6',
              'target-arrow-shape': 'triangle',
              'arrow-scale': 0.8,
              'curve-style': 'bezier',
              opacity: 0.8,
              'overlay-opacity': 0,
            },
          },
          {
            selector: 'node.is-selected',
            style: {
              width: 32,
              height: 32,
              label: 'data(gid)',
              'border-width': 4,
              'border-color': '#254edb',
              'outline-color': '#dce6ff',
              'outline-width': 7,
              'outline-opacity': 0.65,
              'z-index': 10,
            },
          },
          {
            selector: 'node.is-hovered',
            style: { label: 'data(gid)', 'border-color': '#7189b5', 'z-index': 20 },
          },
          {
            selector: 'edge.is-adjacent',
            style: { width: 2, 'line-color': '#829cca', 'target-arrow-color': '#6c88b9', opacity: 1 },
          },
        ],
      });
      cyRef.current = cy;
      const select = (event: EventObject) => {
        if (!disabledRef.current) onSelectRef.current(event.target.data('gid') as string);
      };
      const enter = (event: EventObject) => {
        if (disabledRef.current) return;
        event.target.addClass('is-hovered');
        container.style.cursor = 'pointer';
      };
      const leave = (event: EventObject) => {
        event.target.removeClass('is-hovered');
        container.style.cursor = '';
      };
      cy.on('tap', 'node', select);
      cy.on('mouseover', 'node', enter);
      cy.on('mouseout', 'node', leave);
      const resizeObserver = new ResizeObserver(() => cy.resize());
      resizeObserver.observe(container);

      disposeRenderer = () => {
        resizeObserver.disconnect();
        cy.off('tap', 'node', select);
        cy.off('mouseover', 'node', enter);
        cy.off('mouseout', 'node', leave);
        cy.destroy();
      };
      setRenderer(cy);
    }).catch(() => {
      if (!disposed) setRendererError(true);
    });

    return () => {
      disposed = true;
      disposeRenderer?.();
      cyRef.current = null;
      topologyRef.current = null;
      setRenderer(null);
    };
  }, [hasNodes, rendererAttempt]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (!graph) {
      cy.elements().remove();
      topologyRef.current = null;
      return;
    }
    const topology = JSON.stringify([
      graph.run_id,
      graph.nodes.map((node) => node.gid).sort(compareIds),
      graph.edges.map((edge) => edgeId(edge.source, edge.target)).sort(compareIds),
    ]);
    if (topologyRef.current === topology) {
      cy.batch(() => {
        for (const node of graph.nodes) cy.getElementById(nodeId(node.gid)).data('color', ROLE_COLORS[node.role]);
      });
      return;
    }
    const positions = positionsFor(graph);
    const elements: ElementDefinition[] = [
      ...[...graph.nodes].sort((a, b) => compareIds(a.gid, b.gid)).map((node) => ({
        data: { id: nodeId(node.gid), gid: node.gid, color: ROLE_COLORS[node.role] },
        position: positions.get(nodeId(node.gid)),
      })),
      ...graph.edges.map((edge) => ({
        data: { id: edgeId(edge.source, edge.target), source: nodeId(edge.source), target: nodeId(edge.target) },
      })),
    ];
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });
    topologyRef.current = topology;
    cy.resize();
    fitGraph(cy);
  }, [graph, renderer]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().removeClass('is-selected');
      cy.edges().removeClass('is-adjacent');
      if (selectedGid !== null) {
        const selected = cy.getElementById(nodeId(selectedGid));
        selected.addClass('is-selected');
        selected.connectedEdges().addClass('is-adjacent');
      }
    });
  }, [selectedGid, graph, renderer]);

  const zoomBy = (factor: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({
      level: Math.max(cy.minZoom(), Math.min(cy.maxZoom(), cy.zoom() * factor)),
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  };

  return (
    <section className="graph-panel" aria-labelledby={headingId} aria-busy={loading || rendererLoading}>
      <header className="graph-header">
        <div>
          <h2 id={headingId}>Наблюдаемые связи</h2>
          <p className="graph-scope">{scopeLabel}</p>
        </div>
        <div className="graph-radius" role="group" aria-label="Радиус окружения выбранного узла">
          <span>Радиус</span>
          {([1, 2] as const).map((value) => (
            <button key={value} type="button" className={radius === value ? 'is-active' : ''}
              aria-pressed={radius === value} disabled={selectedGid === null || loading}
              onClick={() => onRadiusChange(value)} title={`${value === 1 ? 'Один переход' : 'Два перехода'} от выбранного узла`}>
              {value}
            </button>
          ))}
        </div>
      </header>
      <div className="graph-stage">
        <div ref={canvasRef} className="graph-canvas" role="img"
          aria-label={graph ? `Направленный граф. Узлов: ${graph.shown_nodes}, связей: ${graph.edges.length}. Выбор узлов и текстовое описание направленных связей доступны под графом.` : 'Область графа. Данные ещё не загружены.'}
          aria-hidden={unavailable} />
        {!unavailable ? (
          <>
            <div className="graph-direction"><ArrowRight size={14} aria-hidden="true" /> Направление перевода</div>
            <div className="graph-toolbar" role="group" aria-label="Масштаб графа">
              <button type="button" className="icon-button" aria-label="Увеличить граф" title="Увеличить" onClick={() => zoomBy(1.25)}><Plus size={17} /></button>
              <button type="button" className="icon-button" aria-label="Уменьшить граф" title="Уменьшить" onClick={() => zoomBy(0.8)}><Minus size={17} /></button>
              <button type="button" className="icon-button" aria-label="Показать граф целиком" title="Показать целиком" onClick={() => { if (cyRef.current) fitGraph(cyRef.current); }}><Expand size={17} /></button>
            </div>
          </>
        ) : null}
        {loading ? (
          <div className="graph-overlay" role="status"><LoaderCircle className="spin" size={26} aria-hidden="true" /><h3>Загружаем связи</h3><p>Получаем выбранную область графа.</p></div>
        ) : error ? (
          <div className="graph-overlay" role="alert"><Network size={30} aria-hidden="true" /><h3>Граф недоступен</h3><p>{error}</p><button className="button button-secondary" type="button" onClick={onRetry}><RotateCcw size={15} aria-hidden="true" />Повторить</button></div>
        ) : !hasNodes ? (
          <div className="graph-overlay"><div className="graph-empty-symbol"><Network size={42} strokeWidth={1.25} aria-hidden="true" /></div><h3>{graph ? 'В выбранной области нет узлов' : 'Каждое исследование начинается с узла'}</h3><p>{graph ? 'Выберите другой узел, кластер или компоненту.' : 'Найдите точный gid или выберите узел в списке приоритетов, чтобы увидеть его связи.'}</p></div>
        ) : rendererError ? (
          <div className="graph-overlay" role="alert"><Network size={30} aria-hidden="true" /><h3>Не удалось отобразить граф</h3><p>Повторите попытку или обновите страницу. Список узлов и связей доступен ниже.</p><button className="button button-secondary" type="button" onClick={() => setRendererAttempt((attempt) => attempt + 1)}><RotateCcw size={15} aria-hidden="true" />Повторить отображение</button></div>
        ) : rendererLoading ? (
          <div className="graph-overlay" role="status"><LoaderCircle className="spin" size={26} aria-hidden="true" /><h3>Подготавливаем граф…</h3></div>
        ) : null}
      </div>
      <div className="graph-legend" aria-label="Легенда ролей">
        {roles.map((role) => <span key={role}><i style={{ backgroundColor: ROLE_COLORS[role] }} aria-hidden="true" />{ROLE_LABELS[role]}</span>)}
      </div>
      <footer className="graph-footer">
        <div aria-live="polite">
          {graph && !loading && !error ? <p className={graph.truncated ? 'graph-truncated' : 'graph-count'}>{graph.truncated ? `Показано ${graph.shown_nodes} из ${graph.total_nodes} узлов выбранной области` : `Узлов: ${graph.shown_nodes}. Направленных связей: ${graph.edges.length}.`}</p> : null}
          {graph && graph.nodes.length === 1 && graph.edges.length === 0 && !loading && !error ? <p>У этого узла нет связей в выбранной наблюдаемой области.</p> : null}
        </div>
        <p>Метрики карточки рассчитаны на всём наблюдаемом графе. Роли — гипотезы по доступным данным.</p>
        {hasNodes ? (
          <details className="graph-node-list">
            <summary>Выбрать узел с клавиатуры</summary>
            <label htmlFor={selectorId}>Узлы показанной области</label>
            <select id={selectorId} value={graph?.nodes.some((node) => node.gid === selectedGid) ? selectedGid ?? '' : ''}
              disabled={loading || Boolean(error)} onChange={(event) => { if (event.target.value) onSelectNode(event.target.value); }}>
              <option value="" disabled>Выберите gid</option>
              {graph?.nodes.map((node) => <option key={node.gid} value={node.gid}>{node.gid} — {ROLE_LABELS[node.role]}{node.assignment_status === 'insufficient_evidence' ? ' (недостаточно данных)' : ''}</option>)}
            </select>
          </details>
        ) : null}
        {graph && hasNodes ? <GraphEdges key={`${graph.run_id}:${scopeLabel}:${radius}`} graph={graph} disabled={loading || Boolean(error)} onSelectNode={onSelectNode} /> : null}
      </footer>
    </section>
  );
}
