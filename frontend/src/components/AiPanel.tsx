import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowUpRight, ChevronDown, LoaderCircle, RefreshCw, Search, Sparkles, X } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { AIResponse, Target } from '../types/api';

type AiPanelProps = {
  runId: string;
  target: Target;
  onRunConflict: () => void;
};

type AiRequest = { mode: 'explain' } | { mode: 'investigate'; question: string };
type AiState = {
  scope: string;
  status: 'idle' | 'loading' | 'error' | 'success';
  request: AiRequest | null;
  result: AIResponse | null;
  error: string | null;
  conflict: boolean;
};

const initialState = (scope: string): AiState => ({ scope, status: 'idle', request: null, result: null, error: null, conflict: false });

function ResponseDetails({ result }: { result: AIResponse }) {
  const fallback = result.status === 'fallback';
  return (
    <div className="ai-response">
      <div className={`ai-response-label ${fallback ? 'fallback-label' : ''}`}><span className="status-dot" />{fallback ? 'Локальная справка (fallback)' : 'Ответ AI по данным запуска'}</div>
      {fallback ? <p className="fallback-reason">{result.fallback_reason ?? 'AI-ответ недоступен. Показано локальное объяснение по рассчитанным данным.'}</p> : null}
      <p className="ai-summary">{result.summary}</p>

      {result.claims.length > 0 ? <section className="ai-result-section"><h4>Выводы и основания</h4><ol className="ai-claims">{result.claims.map((claim, index) => <li key={index}><p>{claim.text}</p><div className="evidence-chips" aria-label="Идентификаторы оснований">{claim.evidence_ids.length > 0 ? claim.evidence_ids.map((evidenceId, evidenceIndex) => <code key={`${evidenceId}-${evidenceIndex}`}>{evidenceId}</code>) : <span className="fine-print">Основания не переданы</span>}</div></li>)}</ol></section> : null}

      <section className="ai-result-section"><h4>Выполненные проверки <span className="section-count">{result.checks.length}</span></h4>
        {result.checks.length > 0 ? <div className="ai-checks">{result.checks.map((check, index) => <details className="ai-check" key={`${check.evidence_id}-${index}`}><summary><Search size={13} aria-hidden="true" /><code>{check.tool}</code><ChevronDown size={13} className="check-chevron" aria-hidden="true" /></summary><div className="ai-check-body"><p className="metric-label">Основание</p><code className="check-evidence-id">{check.evidence_id}</code><p className="metric-label">Аргументы</p><pre>{JSON.stringify(check.args, null, 2)}</pre><p className="metric-label">Результат</p><pre>{JSON.stringify(check.result, null, 2)}</pre></div></details>)}</div> : <p className="empty-note">Дополнительные функции не выполнялись.</p>}
      </section>

      <section className="ai-result-section"><h4>Ограничения</h4>{result.limitations.length > 0 ? <ul className="limitations-list">{result.limitations.map((limitation, index) => <li key={`${limitation}-${index}`}>{limitation}</li>)}</ul> : <p className="empty-note">Дополнительные ограничения в ответе не указаны.</p>}</section>
      <p className="fine-print">AI дополняет объяснение. Формальные роль и баллы остаются результатом расчёта. Ссылки на основания сами по себе не подтверждают все выводы свободного текста.</p>
    </div>
  );
}

export function AiPanel({ runId, target, onRunConflict }: AiPanelProps) {
  const scope = JSON.stringify([runId, target.kind, target.id]);
  const [state, setState] = useState<AiState>(() => initialState(scope));
  const [questionState, setQuestionState] = useState({ scope, text: '' });
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const currentScope = useRef(scope);
  const visible = state.scope === scope ? state : initialState(scope);
  const question = questionState.scope === scope ? questionState.text : '';
  const loading = visible.status === 'loading';

  useEffect(() => {
    currentScope.current = scope;
    sequence.current += 1;
    controller.current?.abort();
    setState(initialState(scope));
    setQuestionState({ scope, text: '' });
    return () => {
      sequence.current += 1;
      controller.current?.abort();
    };
  }, [scope]);

  async function submit(request: AiRequest) {
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    const requestSequence = ++sequence.current;
    const requestScope = scope;
    const requestTarget: Target = { kind: target.kind, id: target.id };
    const isCurrent = () => !requestController.signal.aborted && sequence.current === requestSequence && currentScope.current === requestScope;
    setState({ ...initialState(scope), status: 'loading', request });

    try {
      const result = request.mode === 'explain'
        ? await api.explain(runId, requestTarget, requestController.signal)
        : await api.investigate(runId, requestTarget, request.question, requestController.signal);
      if (!isCurrent()) return;
      if (result.run_id !== runId) {
        setState({ ...initialState(requestScope), status: 'error', request, conflict: true, error: 'Набор данных изменился. Обновите данные и повторите запрос для нового запуска.' });
        onRunConflict();
        return;
      }
      setState({ ...initialState(requestScope), status: 'success', request, result });
    } catch (error) {
      if (!isCurrent()) return;
      const conflict = error instanceof ApiError && error.status === 409;
      const message = conflict
        ? 'Набор данных изменился. Обновите данные и повторите запрос для нового запуска.'
        : error instanceof Error ? error.message : 'Не удалось получить ответ. Проверьте доступность сервера и повторите запрос.';
      setState({ ...initialState(requestScope), status: 'error', request, error: message, conflict });
      if (conflict) onRunConflict();
    } finally {
      if (controller.current === requestController) controller.current = null;
    }
  }

  function cancel() {
    sequence.current += 1;
    controller.current?.abort();
    controller.current = null;
    setState(initialState(scope));
  }

  function investigate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (text && !loading) void submit({ mode: 'investigate', question: text });
  }

  return (
    <section className="ai-panel" aria-labelledby="ai-panel-title">
      <header className="ai-header"><h3 id="ai-panel-title"><Sparkles size={16} aria-hidden="true" />AI-помощник</h3><span className="ai-tag">По основаниям</span></header>
      <p className="ai-context">{target.kind === 'node' ? 'Узел' : 'Кластер'} <code>{target.id}</code></p>
      <button type="button" className="ai-explain-button" disabled={loading || visible.conflict} onClick={() => void submit({ mode: 'explain' })}><Sparkles size={15} aria-hidden="true" />Объяснить{loading && visible.request?.mode === 'explain' ? <LoaderCircle size={15} className="spinner" aria-hidden="true" /> : <ArrowUpRight size={14} aria-hidden="true" />}</button>

      <form className="ai-question-form" onSubmit={investigate}>
        <label htmlFor="ai-question">Исследовать вопрос</label>
        <textarea id="ai-question" rows={3} value={question} onChange={event => setQuestionState({ scope, text: event.target.value })} placeholder="Например: насколько исходящие сосредоточены у одного получателя?" disabled={loading || visible.conflict} />
        <div className="ai-form-footer"><span className="fine-print">До 3 проверок по данным</span><button type="submit" className="ai-investigate-button" disabled={loading || visible.conflict || !question.trim()}><Search size={14} aria-hidden="true" />Исследовать</button></div>
      </form>

      {loading ? <div className="ai-loading" role="status"><LoaderCircle size={17} className="spinner" aria-hidden="true" /><p>{visible.request?.mode === 'investigate' ? 'Проверяем вопрос по данным…' : 'Готовим объяснение…'}<span>Обычно до 30 секунд</span></p><button type="button" className="icon-button" aria-label="Отменить AI-запрос" title="Отменить запрос" onClick={cancel}><X size={15} /></button></div> : null}
      {visible.error ? <div className="ai-error" role="alert"><AlertCircle size={16} aria-hidden="true" /><div><p>{visible.error}</p>{visible.conflict ? <button type="button" className="text-button" onClick={onRunConflict}><RefreshCw size={13} aria-hidden="true" />Обновить данные</button> : <button type="button" className="text-button" onClick={() => { if (visible.request) void submit(visible.request); }}><RefreshCw size={13} aria-hidden="true" />Повторить запрос</button>}</div></div> : null}
      {visible.result ? <ResponseDetails result={visible.result} /> : null}
      {visible.status === 'idle' ? <p className="fine-print ai-idle-note">Локальные объяснения в карточке доступны независимо от AI.</p> : null}
    </section>
  );
}
