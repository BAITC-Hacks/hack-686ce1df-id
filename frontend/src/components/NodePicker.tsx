import { useEffect, useId, useState } from 'react';
import { searchNames } from '../names';
import type { NodeName } from '../types/demo';

type Props = { label: string; nodes: readonly NodeName[]; value: string; onChange: (gid: string) => void; disabled: boolean; error?: string };
export function NodePicker({ label, nodes, value, onChange, disabled, error }: Props) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const selected = nodes.find(node => node.gid === value);
  const matches = searchNames(nodes, query);
  useEffect(() => { if (open && active >= 0) document.getElementById(`${id}-option-${active}`)?.scrollIntoView?.({ block: 'nearest' }); }, [active, open, id]);
  const choose = (node: NodeName) => { onChange(node.gid); setQuery(''); setOpen(false); setActive(-1); };
  return <div className="demo-field node-picker">
    <label htmlFor={id}>{label}</label>
    <input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open && matches.length > 0}
      aria-controls={`${id}-options`} aria-activedescendant={open && active >= 0 && matches[active] ? `${id}-option-${active}` : undefined}
      aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined}
      autoComplete="off" placeholder="Имя или ID узла" disabled={disabled}
      value={open ? query : selected ? `${selected.display_name} · ${selected.gid}` : query}
      onFocus={() => { setOpen(true); setQuery(''); }}
      onChange={event => { setQuery(event.target.value); setOpen(true); setActive(-1); if (value) onChange(''); }}
      onBlur={() => setOpen(false)}
      onKeyDown={event => {
        if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => Math.min(index + 1, matches.length - 1)); }
        if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => Math.max(index - 1, 0)); }
        if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); }
        if (event.key === 'Enter' && open) {
          event.preventDefault();
          const exact = matches.find(node => node.gid === query.trim());
          const chosen = active >= 0 ? matches[active] : exact ?? (matches.length === 1 ? matches[0] : null);
          if (chosen) choose(chosen);
        }
      }} />
    {open && query.trim() ? <div className="picker-results" id={`${id}-options`} role="listbox" aria-label={`${label}: результаты`}>
      {matches.map((node, index) => <button type="button" role="option" aria-selected={active === index || value === node.gid}
        id={`${id}-option-${index}`} key={node.gid} tabIndex={-1} aria-label={`${node.display_name} · ${node.gid}`}
        onMouseDown={event => event.preventDefault()} onClick={() => choose(node)}><span>{node.display_name}</span><small>ID: {node.gid}</small></button>)}
      {matches.length === 0 ? <p>Узел не найден. Сначала добавьте его.</p> : null}
    </div> : null}
    {selected ? <small className="selected-node">Выбран ID: {selected.gid}</small> : null}
    {error ? <p className="field-error" id={`${id}-error`}>{error}</p> : null}
  </div>;
}
