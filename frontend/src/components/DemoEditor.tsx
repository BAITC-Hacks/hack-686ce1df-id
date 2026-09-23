import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { X, Plus, ArrowRightLeft, LoaderCircle } from 'lucide-react';
import { useDemoMutation } from '../state/useDemoMutation';
import { DEMO_LIMITS, type DemoInfo, type DemoQuality, type NodeDraft } from '../types/demo';
import { NodePicker } from './NodePicker';

export type EditorMode = 'node' | 'transfer' | null;
type Props = { mode: EditorMode; info: DemoInfo | null; onClose: () => void; onSaved: (gid: string) => Promise<boolean>; onConflict: () => Promise<string | null> };
const initialNode = { name: '', gid: '', group: '', groupName: '', is_seed: '', outbound_censored: '', inbound_incomplete: '', hop_depth: '' };
const initialTransfer = { source: '', target: '', amount: '', date: '2026-07-15' };
const booleanValue = (value: string): boolean | null => value === '' ? null : value === 'true';

export function canonicalAmount(value: string, min = DEMO_LIMITS.min_amount_kzt, max = DEMO_LIMITS.max_amount_kzt): string | null {
  const normalized = value.trim().replace(',', '.');
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) return null;
  const tiyn = (money: string) => { const [whole, fraction = ''] = money.split('.'); return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0')); };
  const amount = tiyn(normalized);
  if (amount < tiyn(min) || amount > tiyn(max)) return null;
  return `${amount / 100n}.${String(amount % 100n).padStart(2, '0')}`;
}

export function DemoEditor({ mode, info, onClose, onSaved, onConflict }: Props) {
  const [kind, setKind] = useState<'node' | 'transfer'>('node');
  const [snapshot, setSnapshot] = useState<DemoInfo | null>(null);
  const [draftRun, setDraftRun] = useState<string | null>(null);
  const [node, setNode] = useState(initialNode);
  const [transfer, setTransfer] = useState(initialTransfer);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const priorMode = useRef<EditorMode>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const mutation = useDemoMutation(onSaved);
  const locked = mutation.pending || mutation.uncertain || Boolean(mutation.saved) || refreshing;
  const limits = snapshot?.limits ?? DEMO_LIMITS;

  useEffect(() => {
    if (mode && !priorMode.current) {
      if (!locked && !mutation.conflict) {
        setKind(mode);
        if (!draftRun || kind !== mode) { setDraftRun(info?.run_id ?? null); setSnapshot(info); }
      }
      const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const frame = requestAnimationFrame(() => (panel.current?.querySelector<HTMLElement>('input') ?? panel.current?.querySelector<HTMLElement>('button'))?.focus());
      priorMode.current = mode;
      return () => { cancelAnimationFrame(frame); trigger?.focus(); };
    }
    priorMode.current = mode;
    // Opening alone establishes the draft version. Refreshes must not replace it silently.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);
  useEffect(() => {
    if (info && info.run_id === draftRun) setSnapshot(info);
  }, [info, draftRun]);

  const finish = (success: boolean) => {
    if (!success) return;
    mutation.reset(); setNode(initialNode); setTransfer(initialTransfer); setErrors({}); setDraftRun(null); closeRef.current();
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (locked || mutation.conflict || !draftRun) return;
    const next: Record<string, string> = {};
    if (kind === 'node') {
      const name = node.name.trim();
      if (!name || [...name].length > 120 || /[\u0000-\u001f\u007f-\u009f]/u.test(name)) next.name = 'Введите имя от 1 до 120 символов без управляющих знаков.';
      if (node.gid && !/^[A-Za-z0-9_-]{1,64}$/.test(node.gid)) next.gid = 'ID: 1–64 латинские буквы, цифры, дефис или подчёркивание.';
      if (node.group === '__new' && (!node.groupName.trim() || [...node.groupName.trim()].length > 80 || /[\u0000-\u001f\u007f-\u009f]/u.test(node.groupName))) next.groupName = 'Введите название группы от 1 до 80 печатных символов.';
      if (node.hop_depth && (!/^\d+$/.test(node.hop_depth) || !Number.isSafeInteger(Number(node.hop_depth)))) next.hop_depth = 'Глубина — целое неотрицательное число.';
      setErrors(next);
      if (Object.keys(next).length) return;
      const quality: DemoQuality = { is_seed: booleanValue(node.is_seed), outbound_censored: booleanValue(node.outbound_censored), inbound_incomplete: booleanValue(node.inbound_incomplete), hop_depth: node.hop_depth === '' ? null : Number(node.hop_depth) };
      const draft: NodeDraft = { display_name: name, ...(node.gid ? { gid: node.gid } : {}), quality,
        group: node.group === '__new' ? { kind: 'new', name: node.groupName.trim() } : node.group ? { kind: 'existing', id: node.group } : null };
      finish(await mutation.submit('node', draft, draftRun));
    } else {
      if (!transfer.source) next.source = 'Выберите отправителя из списка.';
      if (!transfer.target) next.target = 'Выберите получателя из списка.';
      if (transfer.source && transfer.source === transfer.target) next.target = 'Отправитель и получатель должны различаться.';
      const amount = canonicalAmount(transfer.amount, limits.min_amount_kzt, limits.max_amount_kzt);
      if (amount === null) next.amount = 'Сумма: от 5 000 до 1 000 000 000 000 ₸, не больше двух знаков после запятой.';
      if (!/^2026-07-(0[1-9]|[12]\d|3[01])$/.test(transfer.date) || transfer.date < limits.date_from || transfer.date > limits.date_to) next.date = 'Выберите дату с 1 по 31 июля 2026.';
      setErrors(next);
      if (Object.keys(next).length || amount === null) return;
      finish(await mutation.submit('transfer', { source: transfer.source, target: transfer.target, amount_kzt: amount, date: transfer.date }, draftRun));
    }
  };
  const refreshConflict = async () => {
    setRefreshing(true); setRefreshError(null);
    try {
      const nextRun = await onConflict();
      if (nextRun) { setDraftRun(nextRun); mutation.acknowledgeConflict(); }
      else setRefreshError('Обновление не удалось. Черновик сохранён; попробуйте ещё раз.');
    } catch { setRefreshError('Обновление не удалось. Черновик сохранён; попробуйте ещё раз.'); }
    finally { setRefreshing(false); }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') { event.preventDefault(); onClose(); }
    if (event.key === 'Tab') {
      const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]') ?? []).filter(element => element.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  };
  const fieldError = (key: string) => errors[key] ? <p className="field-error" id={`demo-${key}-error`}>{errors[key]}</p> : null;
  const fieldProps = (key: string) => ({ 'aria-invalid': Boolean(errors[key]), 'aria-describedby': errors[key] ? `demo-${key}-error` : undefined });
  const booleanFields = [['is_seed', 'Исходный узел (seed)'], ['outbound_censored', 'Исходящие ограничены обходом'], ['inbound_incomplete', 'Входящие неполны']] as const;

  return <div className="demo-editor-layer" hidden={mode === null}>
    <div className="demo-editor-backdrop" onClick={onClose} aria-hidden="true" />
    <div className="demo-editor" role="dialog" aria-modal="true" aria-labelledby="demo-editor-title" ref={panel} onKeyDown={onKeyDown}>
      <header className="editor-heading"><div><p className="eyebrow">Искусственные данные</p><h2 id="demo-editor-title">{kind === 'node' ? 'Добавить узел' : 'Добавить перевод'}</h2></div><button type="button" className="icon-button" aria-label="Закрыть форму" onClick={onClose}><X size={19} /></button></header>
      <p className="editor-intro">{kind === 'node' ? 'Создайте участника демосети. После сохранения откроется его карточка.' : 'Добавьте один перевод. Связи, суммы и приоритеты пересчитаются автоматически.'}</p>
      <form className="demo-form" onSubmit={event => void submit(event)} noValidate>
        <fieldset disabled={locked || mutation.conflict}>
          {kind === 'node' ? <>
            <div className="demo-field"><label htmlFor="demo-name">Имя <span aria-hidden="true">*</span></label><input id="demo-name" value={node.name} onChange={event => setNode({ ...node, name: event.target.value })} placeholder="Айдана Садыкова" required maxLength={240} autoComplete="off" {...fieldProps('name')} />{fieldError('name')}</div>
            <div className="demo-field"><label htmlFor="demo-gid">ID узла <span className="optional">необязательно</span></label><input id="demo-gid" value={node.gid} onChange={event => setNode({ ...node, gid: event.target.value })} placeholder="Назначится автоматически" autoComplete="off" spellCheck={false} {...fieldProps('gid')} /><small>Латинские буквы, цифры, _ и −. Начальные нули сохраняются.</small>{fieldError('gid')}</div>
            <details className="editor-extras"><summary>Группа и полнота наблюдения</summary>
              <div className="demo-field"><label htmlFor="demo-group">Демогруппа</label><select id="demo-group" value={node.group} onChange={event => setNode({ ...node, group: event.target.value })}><option value="">Отдельная группа</option>{snapshot?.groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}<option value="__new">Создать группу…</option></select></div>
              {node.group === '__new' ? <div className="demo-field"><label htmlFor="demo-group-name">Название группы</label><input id="demo-group-name" value={node.groupName} onChange={event => setNode({ ...node, groupName: event.target.value })} {...fieldProps('groupName')} />{fieldError('groupName')}</div> : null}
              {booleanFields.map(([key, label]) => <div className="demo-field" key={key}><label htmlFor={`demo-${key}`}>{label}</label><select id={`demo-${key}`} value={node[key]} onChange={event => setNode({ ...node, [key]: event.target.value })}><option value="">Неизвестно</option><option value="true">Да</option><option value="false">Нет</option></select></div>)}
              <div className="demo-field"><label htmlFor="demo-depth">Глубина обхода</label><input id="demo-depth" inputMode="numeric" value={node.hop_depth} onChange={event => setNode({ ...node, hop_depth: event.target.value })} placeholder="Неизвестно" {...fieldProps('hop_depth')} />{fieldError('hop_depth')}</div>
            </details>
          </> : <>
            <NodePicker label="Отправитель" nodes={snapshot?.nodes ?? []} value={transfer.source} onChange={source => setTransfer(previous => ({ ...previous, source }))} disabled={locked || mutation.conflict} error={errors.source} />
            <NodePicker label="Получатель" nodes={snapshot?.nodes ?? []} value={transfer.target} onChange={target => setTransfer(previous => ({ ...previous, target }))} disabled={locked || mutation.conflict} error={errors.target} />
            <div className="demo-field"><label htmlFor="demo-amount">Сумма, ₸</label><input id="demo-amount" inputMode="decimal" value={transfer.amount} onChange={event => setTransfer({ ...transfer, amount: event.target.value })} placeholder="Например, 12500,50" {...fieldProps('amount')} /><small>Минимум 5 000 ₸. Точка и запятая допустимы.</small>{fieldError('amount')}</div>
            <div className="demo-field"><label htmlFor="demo-date">Дата перевода</label><input id="demo-date" type="date" min={limits.date_from} max={limits.date_to} value={transfer.date} onChange={event => setTransfer({ ...transfer, date: event.target.value })} {...fieldProps('date')} /><small>Демонстрационное окно: июль 2026.</small>{fieldError('date')}</div>
          </>}
        </fieldset>
        {mutation.error ? <div className="editor-message" role="alert">{mutation.error}</div> : null}
        {refreshError ? <div className="editor-message" role="alert">{refreshError}</div> : null}
        {mutation.uncertain ? <p className="editor-message">Результат неизвестен: запись могла сохраниться. Повтор отправит тот же запрос и не создаст дубль. Форму можно закрыть — повтор останется доступен.</p> : null}
        {mutation.saved ? <p className="editor-message" role="status">Изменение сохранено. Повторная запись не требуется.</p> : null}
        <footer className="editor-actions"><button type="button" className="button button-secondary" onClick={onClose}>Отмена</button>
          {mutation.uncertain ? <button type="button" className="button button-primary" disabled={mutation.pending} onClick={() => void mutation.retry().then(finish)}>Повторить сохранение</button>
            : mutation.saved ? <button type="button" className="button button-primary" disabled={mutation.pending} onClick={() => void mutation.retryRefresh().then(finish)}>Обновить данные</button>
              : mutation.conflict ? <button type="button" className="button button-primary" disabled={refreshing} onClick={() => void refreshConflict()}>{refreshing ? 'Обновляем…' : 'Обновить, сохранив черновик'}</button>
                : <button type="submit" className="button button-primary" disabled={mutation.pending || refreshing || !draftRun}>{mutation.pending ? <LoaderCircle size={16} className="spin" /> : kind === 'node' ? <Plus size={16} /> : <ArrowRightLeft size={16} />}{mutation.pending ? 'Сохраняем…' : 'Сохранить'}</button>}
        </footer>
      </form>
    </div>
  </div>;
}
