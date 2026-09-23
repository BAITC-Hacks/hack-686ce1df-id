import { Network, ChevronRight } from 'lucide-react';
import type { ClusterRecord } from '../types/api';
import type { Scope } from '../state/useWorkspace';

export function ClusterList({ clusters, selected, onSelect }: { clusters: ClusterRecord[]; selected: Scope | null; onSelect: (scope: Scope) => void }) {
  const groups = new Map<string, ClusterRecord[]>();
  for (const cluster of clusters) groups.set(cluster.component_id, [...(groups.get(cluster.component_id) ?? []), cluster]);
  return <div className="cluster-list">
    {clusters.length === 0 ? <p className="empty-list">Кластеры пока отсутствуют.</p> : [...groups].map(([component, items]) => <section key={component} className="component-group">
      <button className={`component-button ${selected?.kind === 'component' && selected.id === component ? 'is-selected' : ''}`} onClick={() => onSelect({ kind: 'component', id: component })}><Network size={15} /><span>Компонента {component}</span><ChevronRight size={14} /></button>
      {items.map(cluster => <button className={`cluster-button ${selected?.kind === 'cluster' && selected.id === cluster.cluster_id ? 'is-selected' : ''}`} key={cluster.cluster_id} onClick={() => onSelect({ kind: 'cluster', id: cluster.cluster_id })}>
        <span><strong>{cluster.cluster_id}</strong><small>Узлов: {cluster.gids.length}</small></span>
        <p>{cluster.hypothesis.text}</p>
      </button>)}
    </section>)}
  </div>;
}
