import { useState, type FormEvent } from 'react';
import { Search, ArrowRight } from 'lucide-react';
import { searchNames } from '../names';
import type { NodeName } from '../types/demo';

export function SearchPanel({ onSearch, disabled, nodes = [] }: { onSearch: (gid: string) => void; disabled: boolean; nodes?: readonly NodeName[] }) {
  const [value, setValue] = useState('');
  const [showResults, setShowResults] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const matches = searchNames(nodes, value);
  const choose = (gid: string) => { onSearch(gid); setShowResults(false); setMessage(null); };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const query = value.trim();
    if (!query || disabled) return;
    if (nodes.length === 0) { choose(query); return; }
    const exact = nodes.find(node => node.gid === query);
    if (exact) { choose(exact.gid); return; }
    if (matches.length === 1) { choose(matches[0].gid); return; }
    setShowResults(true);
    setMessage(matches.length ? 'Найдено несколько узлов. Выберите имя и ID.' : 'Узел не найден. Проверьте имя или ID.');
  };
  return <form className="search-panel" onSubmit={submit}>
    <label htmlFor="gid-search">Найти узел</label>
    <div className="search-field">
      <Search size={17} aria-hidden="true" />
      <input id="gid-search" value={value} onChange={event => { setValue(event.target.value); setShowResults(true); setMessage(null); }} placeholder={nodes.length ? 'Имя или ID' : 'Введите точный gid'} autoComplete="off" spellCheck={false} disabled={disabled} />
      <button className="search-submit" type="submit" disabled={disabled || !value.trim()} aria-label="Найти узел"><ArrowRight size={18} /></button>
    </div>
    <p className="muted micro">{nodes.length ? `Поиск по всем ${nodes.length} узлам. Тёзки различаются по ID.` : 'Идентификатор целиком, включая ведущие нули.'}</p>
    {message ? <p className="search-message" role="status">{message}</p> : null}
    {showResults && value.trim() && matches.length > 0 ? <div className="search-results" aria-label="Результаты поиска">{matches.map(node => <button type="button" key={node.gid} onClick={() => choose(node.gid)}><strong>{node.display_name}</strong><small>ID: {node.gid}</small></button>)}</div> : null}
  </form>;
}
