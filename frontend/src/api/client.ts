import type {
  AIResponse,
  ClusterResponse,
  ClustersResponse,
  GraphQuery,
  GraphResponse,
  HealthResponse,
  NodeResponse,
  PrioritiesResponse,
  Target,
} from '../types/api';

const API_BASE = '/api';
const REQUEST_TIMEOUT_MS = 15_000;
// The backend has a 30-second AI budget; allow its fallback to arrive.
const AI_TIMEOUT_MS = 45_000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly runId: string | null;

  constructor(message: string, status: number, code: string, runId: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.runId = runId;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireId(value: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new ApiError('Идентификатор должен быть непустой строкой.', 422, 'INVALID_ID');
  }
  return value;
}

function encodedId(value: string): string {
  return encodeURIComponent(requireId(value));
}

interface RequestOptions {
  signal?: AbortSignal;
  body?: unknown;
  timeoutMs?: number;
  allowNullRunId?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  let timedOut = false;
  if (options.signal?.aborted) abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: options.body === undefined ? 'GET' : 'POST',
      headers: options.body === undefined
        ? { Accept: 'application/json' }
        : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
      cache: 'no-store',
    });

    let data: unknown;
    try {
      data = await response.json();
    } catch (error) {
      if (controller.signal.aborted) throw error;
      throw new ApiError(
        response.status >= 500
          ? 'Сервер данных недоступен. Проверьте, что backend запущен, и повторите запрос.'
          : 'Сервер вернул ответ в неизвестном формате.',
        response.status,
        'INVALID_RESPONSE',
      );
    }

    if (!response.ok) {
      const error = isObject(data) && isObject(data.error) ? data.error : null;
      throw new ApiError(
        typeof error?.message === 'string' ? error.message : `Ошибка сервера (${response.status}).`,
        response.status,
        typeof error?.code === 'string' ? error.code : 'HTTP_ERROR',
        isObject(data) && typeof data.run_id === 'string' ? data.run_id : null,
      );
    }

    if (
      !isObject(data)
      || data.contract_version !== '1.0'
      || !(typeof data.run_id === 'string' && data.run_id.length > 0
        || options.allowNullRunId === true && data.run_id === null)
    ) {
      throw new ApiError(
        'Ответ сервера не соответствует контракту 1.0 или не содержит run_id.',
        response.status,
        'CONTRACT_MISMATCH',
        isObject(data) && typeof data.run_id === 'string' ? data.run_id : null,
      );
    }

    return data as T;
  } catch (error) {
    if (options.signal?.aborted) throw new DOMException('Запрос отменён.', 'AbortError');
    if (timedOut) {
      throw new ApiError('Сервер не ответил вовремя. Повторите запрос.', 0, 'TIMEOUT');
    }
    if (error instanceof ApiError) throw error;
    throw new ApiError('Не удалось связаться с сервером. Проверьте подключение.', 0, 'NETWORK_ERROR');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

function graphSearch(query: GraphQuery): string {
  const selectors = ['gid', 'cluster_id', 'component_id'] as const;
  const selected = selectors.filter((key) => query[key] !== undefined);
  if (selected.length !== 1) {
    throw new ApiError('Выберите ровно один узел, кластер или компоненту.', 422, 'INVALID_GRAPH_QUERY');
  }
  const key = selected[0];
  const params = new URLSearchParams({ [key]: requireId(query[key]!) });
  if (query.radius !== undefined) {
    if (key !== 'gid' || (query.radius !== 1 && query.radius !== 2)) {
      throw new ApiError('Радиус окружения узла должен быть 1 или 2.', 422, 'INVALID_GRAPH_QUERY');
    }
    params.set('radius', String(query.radius));
  }
  const limit = query.limit ?? 300;
  if (!Number.isInteger(limit) || limit < 1 || limit > 300) {
    throw new ApiError('Лимит графа должен быть от 1 до 300 узлов.', 422, 'INVALID_GRAPH_QUERY');
  }
  params.set('limit', String(limit));
  return params.toString();
}

async function aiRequest(
  mode: 'explain' | 'investigate',
  runId: string,
  target: Target,
  signal?: AbortSignal,
  question?: string,
): Promise<AIResponse> {
  requireId(runId);
  requireId(target.id);
  if (target.kind !== 'node' && target.kind !== 'cluster') {
    throw new ApiError('Неизвестный тип объекта для AI.', 422, 'INVALID_TARGET');
  }
  const result = await request<AIResponse>(`/ai/${mode}`, {
    body: {
      run_id: runId,
      target,
      ...(mode === 'investigate' ? { question } : {}),
    },
    signal,
    timeoutMs: AI_TIMEOUT_MS,
  });
  if (result.run_id !== runId) {
    throw new ApiError('Набор данных изменился. Обновите данные перед запросом AI.', 409, 'RUN_MISMATCH', result.run_id);
  }
  return result;
}

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/health', { signal, allowNullRunId: true }),
  node: (gid: string, signal?: AbortSignal) => request<NodeResponse>(`/nodes/${encodedId(gid)}`, { signal }),
  priorities: (signal?: AbortSignal) => request<PrioritiesResponse>('/priorities?limit=20&offset=0', { signal }),
  clusters: (signal?: AbortSignal) => request<ClustersResponse>('/clusters', { signal }),
  cluster: (id: string, signal?: AbortSignal) => request<ClusterResponse>(`/clusters/${encodedId(id)}`, { signal }),
  graph: (query: GraphQuery, signal?: AbortSignal) => request<GraphResponse>(`/graph?${graphSearch(query)}`, { signal }),
  explain: (runId: string, target: Target, signal?: AbortSignal) => aiRequest('explain', runId, target, signal),
  investigate: (runId: string, target: Target, question: string, signal?: AbortSignal) => aiRequest('investigate', runId, target, signal, question),
  exportUrl: (name: string) => `${API_BASE}/exports/${encodedId(name)}`,
};
