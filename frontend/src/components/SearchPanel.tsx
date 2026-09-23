import { useState, type FormEvent } from 'react';
import { Search, ArrowRight } from 'lucide-react';

export function SearchPanel({ onSearch, disabled }: { onSearch: (gid: string) => void; disabled: boolean }) {
  const [value, setValue] = useState('');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value.trim()) onSearch(value.trim());
  };
  return <form className="search-panel" onSubmit={submit}>
    <label htmlFor="gid-search">Найти узел</label>
    <div className="search-field">
      <Search size={17} aria-hidden="true" />
      <input id="gid-search" value={value} onChange={event => setValue(event.target.value)} placeholder="Введите точный gid" autoComplete="off" spellCheck={false} />
      <button className="search-submit" type="submit" disabled={disabled || !value.trim()} aria-label="Найти узел"><ArrowRight size={18} /></button>
    </div>
    <p className="muted micro">Идентификатор целиком, включая ведущие нули.</p>
  </form>;
}
