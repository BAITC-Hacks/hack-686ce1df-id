import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { ClusterRecord, DataSource, GraphResponse, NodeRecord } from '../types/api';

export type Scope = { kind: 'node' | 'cluster' | 'component'; id: string };
type Dataset = { runId: string; dataSource: DataSource | null; priorities: NodeRecord[]; clusters: ClusterRecord[]; total: number };
type Selection = {
  scope: Scope | null;
  node: NodeRecord | null;
  cluster: ClusterRecord | null;
  graph: GraphResponse | null;
  loading: boolean;
  error: string | null;
};
const emptySelection: Selection = { scope: null, node: null, cluster: null, graph: null, loading: false, error: null };

export function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return 'Не удалось связаться с сервером. Проверьте подключение и повторите запрос.';
}

/** A request generation covers the complete card + graph transaction. */
export function useWorkspace() {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [selection, setSelection] = useState<Selection>(emptySelection);
  const [radius, setRadius] = useState<1 | 2>(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const bootRequest = useRef<AbortController | null>(null);
  const selectionRequest = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const bootGeneration = useRef(0);
  const run = useRef<string | null>(null);

  const refresh = useCallback(async (message: string | null = null) => {
    bootRequest.current?.abort();
    selectionRequest.current?.abort();
    generation.current++;
    const ticket = ++bootGeneration.current;
    const controller = new AbortController();
    bootRequest.current = controller;
    run.current = null;
    setDataset(null);
    setSelection(emptySelection);
    setLoading(true);
    setError(null);
    setNotice(message);
    try {
      const health = await api.health(controller.signal);
      if (!health.data_ready || !health.run_id) {
        throw new Error('Сервер доступен, но завершённый расчёт ещё не загружен. Повторите запрос после подготовки данных.');
      }
      const [priorities, clusters] = await Promise.all([api.priorities(controller.signal), api.clusters(controller.signal)]);
      if (controller.signal.aborted || ticket !== bootGeneration.current) return;
      if (priorities.run_id !== health.run_id || clusters.run_id !== health.run_id) {
        throw new Error('Во время загрузки изменился расчёт. Обновите набор данных.');
      }
      run.current = health.run_id;
      setDataset({ runId: health.run_id, dataSource: health.dataSource, priorities: priorities.items, clusters: clusters.items, total: priorities.total });
    } catch (cause) {
      if (controller.signal.aborted || ticket !== bootGeneration.current) return;
      setError(cause instanceof Error ? cause.message : errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && ticket === bootGeneration.current) setLoading(false);
    }
  }, []);

  const onRunConflict = useCallback(() => {
    void refresh('Расчёт изменился. Предыдущая карточка и AI-ответ очищены. Выберите узел из обновлённого набора.');
  }, [refresh]);

  const select = useCallback(async (scope: Scope, nextRadius: 1 | 2 = radius) => {
    const expectedRun = run.current;
    if (!expectedRun) return;
    selectionRequest.current?.abort();
    const controller = new AbortController();
    selectionRequest.current = controller;
    const ticket = ++generation.current;
    setSelection(previous => previous.scope?.kind === scope.kind && previous.scope.id === scope.id
      ? { ...previous, loading: true, error: null }
      : { ...emptySelection, scope, graph: previous.graph, loading: true });
    const current = () => !controller.signal.aborted && ticket === generation.current && run.current === expectedRun;
    try {
      const query = scope.kind === 'node'
        ? { gid: scope.id, radius: nextRadius, limit: 300 }
        : scope.kind === 'cluster' ? { cluster_id: scope.id, limit: 300 } : { component_id: scope.id, limit: 300 };
      const [detail, graph] = await Promise.all([
        scope.kind === 'node' ? api.node(scope.id, controller.signal)
          : scope.kind === 'cluster' ? api.cluster(scope.id, controller.signal) : Promise.resolve(null),
        api.graph(query, controller.signal),
      ]);
      if (!current()) return;
      if (graph.run_id !== expectedRun || (detail && detail.run_id !== expectedRun)) {
        onRunConflict();
        return;
      }
      const node = detail && 'node' in detail ? detail.node : null;
      const cluster = detail && 'cluster' in detail ? detail.cluster : null;
      if ((node && node.gid !== scope.id) || (cluster && cluster.cluster_id !== scope.id)) {
        throw new Error('Ответ сервера относится к другому объекту. Повторите запрос.');
      }
      setSelection({ scope, node, cluster, graph, loading: false, error: null });
    } catch (cause) {
      if (!current()) return;
      if (cause instanceof ApiError && (cause.status === 409 || (cause.runId && cause.runId !== expectedRun))) {
        onRunConflict();
        return;
      }
      const message = cause instanceof ApiError && cause.status === 404
        ? `${scope.kind === 'node' ? 'Узел' : 'Объект'} «${scope.id}» не найден. Проверьте идентификатор.`
        : cause instanceof Error ? cause.message : errorMessage(cause);
      setSelection(previous => cause instanceof ApiError && cause.status === 404
        ? { ...emptySelection, scope, error: message }
        : { ...previous, graph: null, loading: false, error: message });
    }
  }, [radius, onRunConflict]);

  const changeRadius = (value: 1 | 2) => {
    if (value === radius) return;
    setRadius(value);
    if (selection.scope?.kind === 'node') void select(selection.scope, value);
  };

  useEffect(() => {
    void refresh();
    return () => {
      bootRequest.current?.abort();
      selectionRequest.current?.abort();
      generation.current++;
      bootGeneration.current++;
    };
  }, [refresh]);

  return { dataset, selection, radius, loading, error, notice, select, changeRadius, refresh, onRunConflict };
}
