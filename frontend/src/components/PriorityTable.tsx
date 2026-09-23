import { EMPTY_NAMES, nodeLabel } from '../names';
import type { NodeRecord } from '../types/api';
import { ROLE_COLORS, ROLE_LABELS, formatNumber } from '../format';

export function PriorityTable({ nodes, selectedGid, onSelect, names = EMPTY_NAMES }: { nodes: NodeRecord[]; selectedGid: string | null; onSelect: (gid: string) => void; names?: ReadonlyMap<string, string> }) {
  return <div className="priority-list">
    <div className="list-columns"><span>Узел / роль</span><span>Приоритет</span></div>
    {nodes.length === 0 ? <p className="empty-list">В текущем расчёте нет узлов.</p> : nodes.map((node, index) => <button key={node.gid} className={`priority-row ${selectedGid === node.gid ? 'is-selected' : ''}`} onClick={() => onSelect(node.gid)} aria-pressed={selectedGid === node.gid}>
      <span className="rank">{index + 1}</span>
      <span className="priority-identity"><strong title={nodeLabel(node.gid, names)}>{names.get(node.gid) ?? node.gid}</strong>{names.has(node.gid) ? <small className="node-gid">ID: {node.gid}</small> : null}<span><i className="role-dot" style={{ background: ROLE_COLORS[node.role] }} />{node.assignment_status === 'insufficient_evidence' ? 'Недостаточно данных' : ROLE_LABELS[node.role]}</span></span>
      <span className="priority-value">{formatNumber(node.priority_score)}<small>/ 100</small></span>
    </button>)}
    <p className="list-note">Приоритет задаёт порядок проверки. Он не является вероятностью виновности.</p>
  </div>;
}
