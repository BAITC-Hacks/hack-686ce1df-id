import { EMPTY_NAMES, nodeLabel } from '../names';
import { ArrowUpRight, GitBranch, ShieldAlert, Users } from 'lucide-react';
import type { ClusterRecord } from '../types/api';

type ClusterDetailsProps = {
  cluster: ClusterRecord;
  names?: ReadonlyMap<string, string>;
  onSelectNode: (gid: string) => void;
  onSelectComponent: (id: string) => void;
};

export function ClusterDetails({ cluster, names = EMPTY_NAMES, onSelectNode, onSelectComponent }: ClusterDetailsProps) {
  return (
    <article className="cluster-details">
      <header className="detail-header">
        <div><p className="eyebrow">Карточка кластера</p><h2 className="detail-title">{cluster.cluster_id}</h2></div>
        <span className="count-badge"><Users size={14} aria-hidden="true" />{cluster.gids.length.toLocaleString('ru-RU')}</span>
      </header>
      <div className="detail-links"><button type="button" className="text-button" onClick={() => onSelectComponent(cluster.component_id)}><GitBranch size={13} aria-hidden="true" />Компонента {cluster.component_id}<ArrowUpRight size={12} aria-hidden="true" /></button></div>
      <p className="fine-print">Кластер выделен внутри слабосвязной компоненты. Это разные уровни группировки узлов.</p>

      <section className="detail-section">
        <h3>Гипотеза назначения</h3>
        <p className="explanation-text">{cluster.hypothesis.text}</p>
        <p className="metric-label">Основания формального расчёта</p>
        {cluster.hypothesis.basis_rule_ids.length > 0 ? <div className="evidence-chips">{cluster.hypothesis.basis_rule_ids.map((ruleId, index) => <code key={`${ruleId}-${index}`}>{ruleId}</code>)}</div> : <p className="empty-note">Правила-основания не переданы.</p>}
      </section>

      <section className="detail-section quality-section">
        <h3><ShieldAlert size={15} aria-hidden="true" />Ограничения гипотезы</h3>
        {cluster.hypothesis.limitations.length > 0 ? <ul className="limitations-list">{cluster.hypothesis.limitations.map((limitation, index) => <li key={`${limitation}-${index}`}>{limitation}</li>)}</ul> : <p className="empty-note">Дополнительные ограничения не переданы.</p>}
        <p className="fine-print">Гипотеза относится к наблюдаемым переводам и не устанавливает фактическое назначение группы.</p>
      </section>

      <section className="detail-section">
        <h3>Узлы кластера <span className="section-count">{cluster.gids.length.toLocaleString('ru-RU')}</span></h3>
        {cluster.gids.length > 0 ? <div className="cluster-members">{cluster.gids.map(gid => <button type="button" className="member-button" key={gid} onClick={() => onSelectNode(gid)} title={nodeLabel(gid, names)}><span>{names.get(gid) ?? gid}{names.has(gid) ? <small className="node-gid">ID: {gid}</small> : null}</span><ArrowUpRight size={12} aria-hidden="true" /></button>)}</div> : <p className="empty-note">В кластере нет узлов.</p>}
      </section>
    </article>
  );
}
