import { useMemo, useState } from 'react';
import { Activity, ArrowUpDown, Boxes, CircleHelp, FileSearch, Info, LoaderCircle, Network, RefreshCw, Unplug, Plus, ArrowRightLeft } from 'lucide-react';
import { useWorkspace } from './state/useWorkspace';
import { SearchPanel } from './components/SearchPanel';
import { PriorityTable } from './components/PriorityTable';
import { ClusterList } from './components/ClusterList';
import { GraphPanel } from './components/GraphPanel';
import { NodeDetails } from './components/NodeDetails';
import { ClusterDetails } from './components/ClusterDetails';
import { AiPanel } from './components/AiPanel';
import { ExportMenu } from './components/ExportMenu';
import { DemoEditor, type EditorMode } from './components/DemoEditor';
import { nodeLabel } from './names';

export default function App() {
  const workspace = useWorkspace();
  const { dataset, selection, loading, error, notice, radius } = workspace;
  const [editorMode, setEditorMode] = useState<EditorMode>(null);
  const names = useMemo(() => new Map(dataset?.demo.nodes.map(node => [node.gid, node.display_name]) ?? []), [dataset]);
  const [tab, setTab] = useState<'priorities' | 'clusters'>('priorities');
  const selectedGid = selection.scope?.kind === 'node' ? selection.scope.id : null;
  const selectNode = (gid: string) => void workspace.select({ kind: 'node', id: gid });
  const selectCluster = (id: string) => void workspace.select({ kind: 'cluster', id });
  const selectComponent = (id: string) => void workspace.select({ kind: 'component', id });
  const scopeLabel = selection.scope ? `${selection.scope.kind === 'node' ? 'Окружение узла' : selection.scope.kind === 'cluster' ? 'Кластер' : 'Компонента'} ${selection.scope.kind === 'node' ? nodeLabel(selection.scope.id, names) : selection.scope.id}` : 'Выберите область исследования';
  const target = selection.node ? { kind: 'node' as const, id: selection.node.gid } : selection.cluster ? { kind: 'cluster' as const, id: selection.cluster.cluster_id } : null;
  const fixtureMode = dataset?.dataSource != null
    ? dataset.dataSource === 'fixtures'
    : import.meta.env.VITE_DATA_SOURCE === 'fixture';
  const realRun = dataset?.dataSource === 'artifacts' && dataset.runId.startsWith('real-') && !dataset.demo.enabled;

  return <div className="application">
    <a className="skip-link" href="#gid-search">Перейти к поиску</a>
    <header className="app-header">
      <div className="brand"><span className="brand-icon"><Network size={23} strokeWidth={1.7} /></span><div><h1>Граф денег</h1><span>Исследование транзакционной сети</span></div></div>
      <div className="header-context"><span className="context-label">Рабочее пространство</span><span>Аналитические гипотезы</span></div>
      <div className="header-actions">{dataset?.demo.enabled ? <div className="demo-launch-actions"><button className="button button-secondary" onClick={() => setEditorMode('node')}><Plus size={16} />Добавить узел</button><button className="button button-primary" onClick={() => setEditorMode('transfer')}><ArrowRightLeft size={16} />Добавить перевод</button></div> : null}<button className="icon-button" title="Обновить набор данных" aria-label="Обновить набор данных" onClick={() => void workspace.refresh()} disabled={loading}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button><ExportMenu demo={dataset?.demo.enabled ?? false} realRun={realRun} runId={dataset?.runId ?? null} onRunConflict={workspace.onRunConflict} /></div>
    </header>
    <div className="run-bar">
      <span className={`connection-status ${dataset ? 'is-ready' : ''}`}><i />{loading ? 'Подключение к данным' : dataset ? 'Расчёт загружен' : 'Нет подключения к данным'}</span>
      <span className="run-identity">{dataset ? <>Расчёт <strong title={dataset.runId}>{dataset.runId}</strong></> : 'Ожидаем завершённый расчёт'}</span>
      <span className="source-label">{fixtureMode ? 'Искусственные тестовые данные' : realRun ? 'Источник: предоставленные данные задания' : 'Источник: локальный API'}</span>
    </div>
    {fixtureMode ? <div className="notice fixture-notice"><Info size={16} />Режим проверки интерфейса. Данные искусственные и не являются результатами анализа кейса.</div> : null}
    {notice ? <div className="notice" role="status"><Info size={16} />{notice}</div> : null}
    <main className="workspace">
      <aside className="navigation-panel" aria-label="Поиск и списки">
        <SearchPanel nodes={dataset?.demo.nodes} onSearch={selectNode} disabled={!dataset} />
        <div className="navigation-tabs" aria-label="Выбор списка">
          <button className={tab === 'priorities' ? 'is-active' : ''} aria-pressed={tab === 'priorities'} onClick={() => setTab('priorities')}><ArrowUpDown size={15} />Приоритеты</button>
          <button className={tab === 'clusters' ? 'is-active' : ''} aria-pressed={tab === 'clusters'} onClick={() => setTab('clusters')}><Boxes size={15} />Кластеры</button>
        </div>
        <div className="navigation-content">
          {loading ? <div className="panel-status" role="status"><LoaderCircle className="spin" size={23} /><p>Загружаем списки…</p></div> : error ? <div className="panel-status"><Unplug size={27} /><h2>Данные недоступны</h2><p role="alert">{error}</p><button className="button button-secondary" onClick={() => void workspace.refresh()}><RefreshCw size={15} />Повторить</button></div> : dataset ? <>
            <div className="list-heading"><h2>{tab === 'priorities' ? 'Проверить в первую очередь' : 'Структура сети'}</h2><span>{tab === 'priorities' ? `${dataset.priorities.length} из ${dataset.total}` : dataset.clusters.length}</span></div>
            {tab === 'priorities' ? <PriorityTable names={names} nodes={dataset.priorities} selectedGid={selectedGid} onSelect={selectNode} /> : <ClusterList clusters={dataset.clusters} selected={selection.scope} onSelect={scope => void workspace.select(scope)} />}
          </> : null}
        </div>
        <div className="navigation-footer"><CircleHelp size={16} /><p>Сначала выберите узел.<br />Затем проверьте связи и основания.</p></div>
      </aside>
      <GraphPanel names={names} graph={selection.graph} selectedGid={selectedGid} loading={selection.loading || loading} onSelectNode={selectNode} radius={radius} onRadiusChange={workspace.changeRadius} scopeLabel={scopeLabel} error={selection.error || error} onRetry={() => selection.scope && dataset ? void workspace.select(selection.scope) : void workspace.refresh()} />
      <aside className="details-panel" aria-label="Карточка и AI-помощник" aria-busy={selection.loading}>
        <div className="details-panel-heading"><FileSearch size={17} /><h2>Детали исследования</h2>{target ? <span className="small-tag">{target.kind === 'node' ? 'Узел' : 'Кластер'}</span> : null}</div>
        <div className="details-content">
          {selection.loading ? <div className="panel-status" role="status"><LoaderCircle className="spin" size={24} /><p>Загружаем карточку…</p></div> : selection.node ? <NodeDetails displayName={names.get(selection.node.gid)} node={selection.node} onSelectCluster={selectCluster} onSelectComponent={selectComponent} /> : selection.cluster ? <ClusterDetails names={names} cluster={selection.cluster} onSelectNode={selectNode} onSelectComponent={selectComponent} /> : <div className="details-empty">
            <span className="detail-empty-icon"><FileSearch size={29} strokeWidth={1.4} /></span>
            <h2>{selection.scope?.kind === 'component' && !selection.error ? `Компонента ${selection.scope.id}` : 'От связей к основаниям'}</h2>
            <p>{selection.error || (selection.scope?.kind === 'component' ? 'Выберите узел на графе, чтобы изучить его роль и основания приоритета.' : 'Найдите узел по gid или выберите его в списке. Здесь появятся роль, показатели и объяснение расчёта.')}</p>
            <div className="empty-flow"><span><Network size={16} />Связи</span><span><Activity size={16} />Показатели</span><span><FileSearch size={16} />Основания</span></div>
          </div>}
          {target && dataset && !selection.loading ? <AiPanel key={`${dataset.runId}:${target.kind}:${target.id}`} runId={dataset.runId} target={target} onRunConflict={workspace.onRunConflict} /> : null}
        </div>
      </aside>
    </main>
    <DemoEditor mode={editorMode} info={dataset?.demo ?? null} onClose={() => setEditorMode(null)} onSaved={workspace.refreshAndSelect} onConflict={() => workspace.refresh()} />
    <footer className="app-footer"><span><Info size={13} />Роли и назначения — гипотезы в пределах наблюдаемой сети.</span><span>Контракт 1.0</span></footer>
  </div>;
}
