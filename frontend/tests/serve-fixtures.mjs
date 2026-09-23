// Browser QA only. This server never reads case data or runs analytical rules.
// Start manually: node tests/serve-fixtures.mjs; point Vite's API_PROXY_TARGET at port 8010.
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';

const RUN_ID = 'fixture-ui-only';
const ARTIFICIAL = 'ИСКУССТВЕННЫЕ ДАННЫЕ: только проверка интерфейса, не результаты анализа.';
const envelope = (body) => ({ contract_version: '1.0', run_id: RUN_ID, ...body });
const decimal = (cents) => `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`;
const cents = (value) => BigInt(value.replace('.', ''));
const compareGid = (a, b) => a.gid < b.gid ? -1 : a.gid > b.gid ? 1 : 0;
const byPriority = (a, b) => b.priority_score - a.priority_score || compareGid(a, b);

const edges = [
  { source: '0007', target: '0012', amount_kzt: '30000.00', tx_count: 3 },
  { source: '0007', target: '0042', amount_kzt: '70000.00', tx_count: 7 },
  { source: '0012', target: '0042', amount_kzt: '20000.00', tx_count: 2 },
];
const largeGids = Array.from({ length: 320 }, (_, index) => `L${String(index + 1).padStart(4, '0')}`);
for (let index = 0; index < largeGids.length - 1; index++) {
  edges.push({ source: largeGids[index], target: largeGids[index + 1], amount_kzt: '5000.00', tx_count: 1 });
}

function makeNode(gid, componentId, clusterId, role, roleScore, priorityScore, quality = {}) {
  const incoming = edges.filter((edge) => edge.target === gid);
  const outgoing = edges.filter((edge) => edge.source === gid);
  const inCents = incoming.reduce((sum, edge) => sum + cents(edge.amount_kzt), 0n);
  const outCents = outgoing.reduce((sum, edge) => sum + cents(edge.amount_kzt), 0n);
  const insufficient = roleScore === 0;
  return {
    gid, component_id: componentId, cluster_id: clusterId,
    role, role_score: roleScore, priority_score: priorityScore,
    assignment_status: insufficient ? 'insufficient_evidence' : 'rule_matched',
    metrics: {
      in_degree_unique: new Set(incoming.map((edge) => edge.source)).size,
      out_degree_unique: new Set(outgoing.map((edge) => edge.target)).size,
      tx_in_count: incoming.reduce((sum, edge) => sum + edge.tx_count, 0),
      tx_out_count: outgoing.reduce((sum, edge) => sum + edge.tx_count, 0),
      in_amount_kzt: decimal(inCents), out_amount_kzt: decimal(outCents),
      out_in_ratio: inCents > 0n ? Number(outCents) / Number(inCents) : null,
    },
    quality: {
      is_seed: false, hop_depth: null, outbound_censored: false, inbound_incomplete: null,
      ...quality,
      reasons: [ARTIFICIAL, ...(quality.reasons ?? [])],
    },
    role_evidence: [{
      rule_id: 'fixture-display-only', metric: 'out_degree_unique', operator: 'gte',
      actual: outgoing.length, threshold: 1, passed: insufficient ? null : outgoing.length >= 1,
    }],
    priority_evidence: [{
      rule_id: 'fixture-priority-only', metric: 'out_amount_kzt', operator: 'gte',
      actual: decimal(outCents), threshold: '0.00', passed: true,
    }],
    role_explanation: insufficient
      ? 'Недостаточно наблюдаемых данных для более специфической роли. Искусственный пример интерфейса.'
      : `Искусственный пример роли ${role}: назначена вручную для проверки отображения, аналитическое правило не применялось.`,
    priority_explanation: 'Баллы заданы вручную только для проверки сортировки искусственных примеров; это не аналитическая формула.',
  };
}

const nodes = [
  makeNode('0007', 'fixture-small', 'fixture-flow', 'distributor', 85, 92, {
    is_seed: true, hop_depth: 0, inbound_incomplete: true,
    reasons: ['В примере исходящие 30 000 KZT к 0012 и 70 000 KZT к 0042; входящие seed неполны.'],
  }),
  makeNode('0012', 'fixture-small', 'fixture-flow', 'transit', 74, 73, { hop_depth: 1 }),
  makeNode('0042', 'fixture-small', 'fixture-flow', 'peripheral', 0, 68, {
    is_seed: null, hop_depth: 4, outbound_censored: true, inbound_incomplete: null,
    reasons: ['Граница искусственного обхода: нулевые наблюдаемые исходящие не доказывают роль конечного получателя.'],
  }),
  makeNode('0999', 'fixture-isolated', 'fixture-isolated-node', 'peripheral', 0, 8, {
    is_seed: null, outbound_censored: null, inbound_incomplete: null,
    reasons: ['Изолированный искусственный узел для проверки пустого окружения.'],
  }),
  ...largeGids.map((gid, index) => makeNode(
    gid, 'fixture-large', 'fixture-large-chain',
    index === 0 ? 'distributor' : index === largeGids.length - 1 ? 'peripheral' : 'transit',
    index === largeGids.length - 1 ? 0 : 60, 20,
  )),
];
const nodeById = new Map(nodes.map((node) => [node.gid, node]));
const priorities = [...nodes].sort(byPriority);
const clusters = [
  { cluster_id: 'fixture-flow', component_id: 'fixture-small', gids: ['0007', '0012', '0042'] },
  { cluster_id: 'fixture-isolated-node', component_id: 'fixture-isolated', gids: ['0999'] },
  { cluster_id: 'fixture-large-chain', component_id: 'fixture-large', gids: largeGids },
].map((cluster) => ({
  ...cluster,
  hypothesis: {
    text: cluster.component_id === 'fixture-large'
      ? 'Искусственная цепочка из 320 узлов для проверки ограничения отображения до 300.'
      : 'Искусственная группа для проверки интерфейса; гипотеза назначения не рассчитывалась.',
    basis_rule_ids: ['fixture-cluster-only'], limitations: [ARTIFICIAL, 'Кластер и компонента заданы вручную в тесте.'],
  },
}));
const clusterById = new Map(clusters.map((cluster) => [cluster.cluster_id, cluster]));
const neighbors = new Map(nodes.map((node) => [node.gid, new Set()]));
for (const edge of edges) {
  neighbors.get(edge.source).add(edge.target);
  neighbors.get(edge.target).add(edge.source);
}

class HttpError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
const invalid = (message) => new HttpError(422, 'INVALID_PARAMETERS', message);
function required(map, id, kind) {
  const result = map.get(id);
  if (!result) throw new HttpError(404, 'NOT_FOUND', `${kind} «${id}» не найден в искусственном наборе.`);
  return result;
}
function integerParam(params, key, fallback, minimum, maximum) {
  const raw = params.get(key);
  if (raw === null) return fallback;
  if (!/^\d+$/.test(raw)) throw invalid(`Параметр ${key} должен быть целым числом.`);
  const number = Number(raw);
  if (!Number.isSafeInteger(number) || number < minimum || number > maximum) throw invalid(`Недопустимый ${key}.`);
  return number;
}

function graphResponse(params) {
  const selectors = ['gid', 'cluster_id', 'component_id'].filter((key) => params.has(key));
  if (selectors.length !== 1 || params.getAll(selectors[0]).length !== 1) throw invalid('Укажите ровно один gid, cluster_id или component_id.');
  const selector = selectors[0];
  const id = params.get(selector);
  const limit = integerParam(params, 'limit', 300, 1, 300);
  let selected;
  if (selector === 'gid') {
    required(nodeById, id, 'Узел');
    const radius = integerParam(params, 'radius', 1, 1, 2);
    const distance = new Map([[id, 0]]);
    let frontier = [id];
    for (let depth = 1; depth <= radius; depth++) {
      const next = [];
      for (const gid of frontier) for (const neighbor of neighbors.get(gid)) {
        if (!distance.has(neighbor)) { distance.set(neighbor, depth); next.push(neighbor); }
      }
      frontier = next;
    }
    // Distance zero always retains the selected node, including with limit=1.
    selected = [...distance.keys()].map((gid) => nodeById.get(gid))
      .sort((a, b) => distance.get(a.gid) - distance.get(b.gid) || byPriority(a, b));
  } else {
    if (params.has('radius')) throw invalid('Радиус применяется только к gid.');
    if (selector === 'cluster_id') {
      selected = required(clusterById, id, 'Кластер').gids.map((gid) => nodeById.get(gid));
    } else {
      selected = nodes.filter((node) => node.component_id === id);
      if (!selected.length) throw new HttpError(404, 'NOT_FOUND', 'Компонента не найдена в искусственном наборе.');
    }
    selected.sort(byPriority);
  }
  const visible = selected.slice(0, limit);
  const visibleIds = new Set(visible.map((node) => node.gid));
  return envelope({
    nodes: visible, edges: edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    truncated: selected.length > visible.length, total_nodes: selected.length, shown_nodes: visible.length,
  });
}

function concentration(gid) {
  const outgoing = edges.filter((edge) => edge.source === gid).sort((a, b) => {
    const difference = cents(b.amount_kzt) - cents(a.amount_kzt);
    return difference === 0n ? (a.target < b.target ? -1 : a.target > b.target ? 1 : 0) : difference > 0n ? 1 : -1;
  });
  const total = outgoing.reduce((sum, edge) => sum + cents(edge.amount_kzt), 0n);
  return {
    gid, total_out_kzt: decimal(total), top_receiver_gid: outgoing[0]?.target ?? null,
    top_receiver_share: total > 0n ? Number(cents(outgoing[0].amount_kzt)) / Number(total) : null,
    receiver_count: outgoing.length,
  };
}
function aiResponse(body, investigate) {
  if (body === null || typeof body !== 'object' || Array.isArray(body)) throw invalid('Ожидается объект JSON.');
  if (body.run_id !== RUN_ID) throw new HttpError(409, 'STALE_RUN', 'Запрос относится к другому запуску. Обновите набор.');
  if (!body.target || !['node', 'cluster'].includes(body.target.kind) || typeof body.target.id !== 'string') throw invalid('Некорректный target.');
  if (investigate && (typeof body.question !== 'string' || !body.question.trim())) throw invalid('Введите вопрос.');
  const { kind, id } = body.target;
  const record = kind === 'node' ? required(nodeById, id, 'Узел') : required(clusterById, id, 'Кластер');
  const evidenceId = `fixture:${kind}:${id}:${investigate ? 'check' : 'record'}`;
  const tool = kind === 'cluster' ? 'get_cluster' : investigate ? 'check_concentration' : 'get_node';
  const result = tool === 'check_concentration' ? concentration(id) : record;
  return envelope({
    status: 'fallback',
    summary: `${ARTIFICIAL} ${kind === 'node' ? record.role_explanation : record.hypothesis.text}`,
    claims: [{
      text: tool === 'check_concentration'
        ? `В искусственном примере исходящая сумма: ${result.total_out_kzt} KZT; получателей: ${result.receiver_count}.`
        : 'Получена карточка выбранного объекта из искусственного набора для проверки ссылок на основания.',
      evidence_ids: [evidenceId],
    }],
    limitations: [ARTIFICIAL, 'AI-провайдер не вызывается; ответ представляет локальную тестовую справку.', 'Назначение узлов и причинность переводов не устанавливались.'],
    checks: [{ tool, args: kind === 'node' ? { gid: id } : { cluster_id: id }, result, evidence_id: evidenceId }],
    fallback_reason: 'Режим браузерной проверки: внешний AI отключён, используются только искусственные записи.',
  });
}

const files = Object.fromEntries(['nodes', 'edges', 'clusters', 'priorities', 'roles', 'rules', 'audit', 'manifest'].map((name) => [name, `${name}.${['priorities', 'roles'].includes(name) ? 'csv' : 'json'}`]));
const rules = envelope({
  fixture_only: true, warning: ARTIFICIAL, self_transfers: 'not_present',
  rules: [
    { rule_id: 'fixture-display-only', description: 'UI example only; role/score assignments below are fixed, not inferred from this evidence row.' },
    { rule_id: 'fixture-priority-only', description: 'Scores are fixed for ordering tests, not calculated from the illustrative evidence row.' },
    { rule_id: 'fixture-cluster-only', description: 'Explicit membership from clusters.json; no clustering algorithm.' },
  ],
  assignments: nodes.map(({ gid, role, role_score, priority_score, assignment_status }) => ({ gid, role, role_score, priority_score, assignment_status })),
});
const hash = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const counts = { nodes: nodes.length, edges: edges.length, clusters: clusters.length, transactions: edges.reduce((sum, edge) => sum + edge.tx_count, 0) };
const audit = envelope({ fixture_only: true, counts, warnings: [ARTIFICIAL], checks: { references_valid: true, decimal_amounts_from_synthetic_edges: true, no_case_data_loaded: true } });
const manifest = envelope({
  status: 'complete', input_hashes: { 'synthetic-records.json': hash(nodes) }, rules_hash: hash(rules),
  counts, elapsed_seconds: 0, stage_seconds: {}, random_seed: 0, versions: { node: process.version }, files,
  warnings: [ARTIFICIAL, 'Время 0 — тестовое значение, не результат бенчмарка.'],
});
const csvCell = (value) => `"${String(value).replaceAll('"', '""')}"`;
const csv = (columns, rows) => `${[columns.join(','), ...rows.map((row) => columns.map((column) => csvCell(row[column])).join(','))].join('\r\n')}\r\n`;
const exportBodies = {
  nodes, edges, clusters, rules, audit, manifest,
  priorities: csv(['rank', 'gid', 'role', 'role_score', 'priority_score', 'priority_explanation'], priorities.map((node, index) => ({ ...node, rank: index + 1 }))),
  roles: csv(['gid', 'role', 'role_score', 'assignment_status', 'role_explanation'], [...nodes].sort(compareGid)),
};

function json(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(body));
}
async function readBody(request) {
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 16_384) throw invalid('Слишком большой тестовый запрос.');
    chunks.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw invalid('Некорректный JSON.'); }
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1:8010');
    const path = decodeURIComponent(url.pathname);
    if (request.method === 'GET') {
      if (path === '/api/health') return json(response, 200, envelope({ status: 'ok', data_ready: true }));
      if (path.startsWith('/api/nodes/')) return json(response, 200, envelope({ node: required(nodeById, path.slice(11), 'Узел') }));
      if (path === '/api/priorities') {
        const limit = integerParam(url.searchParams, 'limit', 20, 1, 1000);
        const offset = integerParam(url.searchParams, 'offset', 0, 0, Number.MAX_SAFE_INTEGER);
        return json(response, 200, envelope({ items: priorities.slice(offset, offset + limit), total: nodes.length }));
      }
      if (path === '/api/clusters') return json(response, 200, envelope({ items: clusters }));
      if (path.startsWith('/api/clusters/')) return json(response, 200, envelope({ cluster: required(clusterById, path.slice(14), 'Кластер') }));
      if (path === '/api/graph') return json(response, 200, graphResponse(url.searchParams));
      if (path.startsWith('/api/exports/')) {
        const name = path.slice(13);
        if (!Object.hasOwn(files, name)) throw new HttpError(404, 'UNKNOWN_EXPORT', 'Неизвестное имя экспорта.');
        const isCsv = files[name].endsWith('.csv');
        response.writeHead(200, {
          'Content-Type': isCsv ? 'text/csv; charset=utf-8' : 'application/json; charset=utf-8',
          'Content-Disposition': `attachment; filename="${files[name]}"`, 'Cache-Control': 'no-store',
        });
        return response.end(isCsv ? exportBodies[name] : JSON.stringify(exportBodies[name], null, 2));
      }
    }
    if (request.method === 'POST' && ['/api/ai/explain', '/api/ai/investigate'].includes(path)) {
      return json(response, 200, aiResponse(await readBody(request), path.endsWith('/investigate')));
    }
    throw new HttpError(404, 'NOT_FOUND', 'Тестовый маршрут не найден.');
  } catch (error) {
    json(response, error instanceof HttpError ? error.status : 500, {
      error: { code: error instanceof HttpError ? error.code : 'FIXTURE_SERVER_ERROR', message: error.message }, run_id: RUN_ID,
    });
  }
});
server.listen(8010, '127.0.0.1', () => {
  console.log(`\n*** ${ARTIFICIAL} ***\nQA server: http://127.0.0.1:8010\nrun_id=${RUN_ID}; nodes=0007, 0012, 0042, 0999; stress component=fixture-large (300/320).\n`);
});
server.on('error', (error) => { console.error('Fixture QA server:', error.message); process.exitCode = 1; });
