import { ArrowUpRight, Check, CircleHelp, FileCheck2, Minus, Network, ShieldAlert } from 'lucide-react';
import { ROLE_COLORS, ROLE_LABELS, formatMoney } from '../format';
import type { Evidence, NodeRecord } from '../types/api';

type NodeDetailsProps = {
  node: NodeRecord;
  displayName?: string;
  onSelectCluster: (id: string) => void;
  onSelectComponent: (id: string) => void;
};

const METRIC_LABELS: Record<string, string> = {
  in_degree_unique: 'Отправители',
  out_degree_unique: 'Получатели',
  tx_in_count: 'Входящие переводы',
  tx_out_count: 'Исходящие переводы',
  in_amount_kzt: 'Сумма входящих',
  out_amount_kzt: 'Сумма исходящих',
  out_in_ratio: 'Исходящие / входящие',
  volume_kzt: 'Общий объём',
  degree: 'Связи, вход + выход',
  transactions: 'Операции, вход + выход',
  priority_score: 'Приоритет проверки',
  is_seed: 'Исходный узел',
  hop_depth: 'Глубина обхода',
  outbound_censored: 'Исходящие ограничены обходом',
  inbound_incomplete: 'Входящие неполны',
};

const OPERATORS: Record<Evidence['operator'], string> = {
  gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=',
};

function displayValue(value: Evidence['actual']): string {
  if (value === null) return 'Нет данных';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  return String(value);
}

function qualityValue(value: boolean | null): string {
  return value === null ? 'Нет данных' : value ? 'Да' : 'Нет';
}

function EvidenceTable({ evidence, label }: { evidence: Evidence[]; label: string }) {
  if (evidence.length === 0) {
    return <p className="empty-note">Условия для этого объяснения не переданы.</p>;
  }

  return (
    <div className="evidence-table-wrap">
      <table className="evidence-table" aria-label={label}>
        <thead><tr><th>Показатель</th><th>Факт</th><th>Условие</th><th>Выполнено</th></tr></thead>
        <tbody>
          {evidence.map((item, index) => (
            <tr key={`${item.rule_id}-${item.metric}-${index}`}>
              <td><span title={item.metric}>{METRIC_LABELS[item.metric] ?? item.metric}</span><code className="evidence-rule">{item.rule_id}</code></td>
              <td title={displayValue(item.actual)}>{displayValue(item.actual)}</td>
              <td>{OPERATORS[item.operator]} {displayValue(item.threshold)}</td>
              <td>
                <span className={`evidence-status ${item.passed === null ? 'unknown' : item.passed ? 'passed' : 'failed'}`}>
                  {item.passed === null ? <CircleHelp size={13} aria-hidden="true" /> : item.passed ? <Check size={13} aria-hidden="true" /> : <Minus size={13} aria-hidden="true" />}
                  {item.passed === null ? 'Нет данных' : item.passed ? 'Да' : 'Нет'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function NodeDetails({ node, displayName, onSelectCluster, onSelectComponent }: NodeDetailsProps) {
  const { metrics, quality } = node;
  const insufficient = node.assignment_status === 'insufficient_evidence';

  return (
    <article className="node-details">
      <header className="detail-header">
        <div><p className="eyebrow">Карточка узла</p><h2 className="detail-title" title={displayName}>{displayName ?? node.gid}</h2>{displayName ? <p className="node-gid">ID: {node.gid}</p> : null}</div>
        <span className="role-badge" style={{ color: ROLE_COLORS[node.role] }}><span className="role-dot" style={{ background: ROLE_COLORS[node.role] }} />{ROLE_LABELS[node.role]}</span>
      </header>

      <div className="detail-links">
        <button type="button" className="text-button" onClick={() => onSelectCluster(node.cluster_id)}><Network size={13} aria-hidden="true" />Кластер {node.cluster_id}<ArrowUpRight size={12} aria-hidden="true" /></button>
        <button type="button" className="text-button" onClick={() => onSelectComponent(node.component_id)}>Компонента {node.component_id}<ArrowUpRight size={12} aria-hidden="true" /></button>
      </div>

      {insufficient ? <div className="notice notice-warning"><ShieldAlert size={16} aria-hidden="true" /><p><strong>Недостаточно оснований</strong><br />Периферийная роль — техническая остаточная категория: данных для более специфической роли недостаточно.</p></div> : null}

      <div className="score-grid">
        <div className="score-card"><span className="metric-label">Соответствие роли</span><strong>{node.role_score}<small> / 100</small></strong><span className="score-caption">По формальным правилам</span></div>
        <div className="score-card priority"><span className="metric-label">Приоритет проверки</span><strong>{node.priority_score}<small> / 100</small></strong><span className="score-caption">Порядок изучения узлов</span></div>
      </div>
      <p className="fine-print">Баллы описывают соответствие правилам и порядок проверки. Это не вероятности.</p>

      <section className="detail-section">
        <h3>Наблюдаемые переводы</h3>
        <div className="amount-grid">
          <div><span className="metric-label">Входящие</span><strong title={`${metrics.in_amount_kzt} KZT`}>{formatMoney(metrics.in_amount_kzt)}</strong><span>{metrics.tx_in_count.toLocaleString('ru-RU')} переводов · {metrics.in_degree_unique.toLocaleString('ru-RU')} отправителей</span></div>
          <div><span className="metric-label">Исходящие</span><strong title={`${metrics.out_amount_kzt} KZT`}>{formatMoney(metrics.out_amount_kzt)}</strong><span>{metrics.tx_out_count.toLocaleString('ru-RU')} переводов · {metrics.out_degree_unique.toLocaleString('ru-RU')} получателей</span></div>
        </div>
        <dl className="metric-list"><div><dt>Отношение исходящих к входящим</dt><dd title={metrics.out_in_ratio === null ? undefined : String(metrics.out_in_ratio)}>{metrics.out_in_ratio === null ? 'Нет данных' : metrics.out_in_ratio.toLocaleString('ru-RU', { maximumSignificantDigits: 6 })}</dd></div></dl>
        <p className="fine-print">Метрики рассчитаны на всём наблюдаемом графе. Суммы не являются остатком на счёте; отношение сумм не доказывает пересылку тех же денег.</p>
      </section>

      <section className="detail-section">
        <h3><FileCheck2 size={15} aria-hidden="true" />Основания роли</h3>
        <p className="explanation-text">{node.role_explanation}</p>
        <EvidenceTable evidence={node.role_evidence} label="Условия назначения роли" />
      </section>

      <section className="detail-section">
        <h3>Основания приоритета</h3>
        <p className="explanation-text">{node.priority_explanation}</p>
        <EvidenceTable evidence={node.priority_evidence} label="Условия приоритета проверки" />
        <p className="fine-print">Связи условий AND/OR и порядок правил определены конфигурацией расчёта.</p>
      </section>

      <section className="detail-section quality-section">
        <h3><ShieldAlert size={15} aria-hidden="true" />Полнота наблюдения</h3>
        <dl className="metric-list">
          <div><dt>Исходный узел (seed)</dt><dd>{qualityValue(quality.is_seed)}</dd></div>
          <div><dt>Глубина исходного обхода</dt><dd>{quality.hop_depth === null ? 'Нет данных' : quality.hop_depth}</dd></div>
          <div><dt>Исходящие ограничены обходом</dt><dd>{qualityValue(quality.outbound_censored)}</dd></div>
          <div><dt>Входящие неполны</dt><dd>{qualityValue(quality.inbound_incomplete)}</dd></div>
        </dl>
        {quality.reasons.length > 0 ? <ul className="limitations-list">{quality.reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul> : null}
        <p className="fine-print">Неизвестная полнота не означает полные данные. Отсутствие наблюдаемых исходящих на границе обхода не означает, что их нет в реальности.</p>
      </section>
    </article>
  );
}
