import { useEffect, useRef, useState } from 'react';
import { Download, ChevronDown, LoaderCircle } from 'lucide-react';
import { api } from '../api/client';

const files = [
  ['nodes', 'Узлы', 'JSON'], ['edges', 'Связи', 'JSON'], ['clusters', 'Кластеры', 'JSON'],
  ['priorities', 'Приоритеты', 'CSV'], ['roles', 'Роли', 'CSV'], ['rules', 'Правила', 'JSON'],
  ['audit', 'Аудит данных', 'JSON'], ['manifest', 'Паспорт расчёта', 'JSON'],
] as const;

export function ExportMenu({ runId, onRunConflict }: { runId: string | null; onRunConflict: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    setOpen(false);
    setError(null);
    setBusy(null);
    return () => {
      controller.current?.abort();
      controller.current = null;
    };
  }, [runId]);
  const download = async (name: string, extension: string) => {
    if (!runId) return;
    const request = new AbortController();
    controller.current?.abort();
    controller.current = request;
    setBusy(name);
    setError(null);
    const timeout = setTimeout(() => request.abort(), 30_000);
    try {
      const before = await api.health(request.signal);
      if (before.run_id !== runId) { onRunConflict(); return; }
      const response = await fetch(api.exportUrl(name), { signal: request.signal, cache: 'no-store' });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status}). Попробуйте ещё раз.`);
      const blob = await response.blob();
      const after = await api.health(request.signal);
      if (after.run_id !== runId) { onRunConflict(); return; }
      if (request.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${name}.${extension.toLowerCase()}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      if (controller.current === request) setError(cause instanceof Error && cause.name !== 'AbortError' ? cause.message : 'Скачивание прервано. Повторите запрос.');
    } finally {
      clearTimeout(timeout);
      if (controller.current === request) setBusy(null);
    }
  };
  return <div className="exports">
    <button className="button button-secondary" disabled={!runId} aria-expanded={open} aria-controls="export-files" onClick={() => setOpen(!open)}><Download size={16} />Выгрузки<ChevronDown size={14} /></button>
    {open ? <div className="export-popover" id="export-files">
      <p className="popover-title">Файлы текущего расчёта</p>
      {files.map(([name, label, extension]) => <button key={name} aria-label={`Скачать ${name}.${extension.toLowerCase()}`} onClick={() => void download(name, extension)} disabled={busy !== null}>{busy === name ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />}<span>{label}</span><small>{extension}</small></button>)}
      {error ? <p className="error-text" role="alert">{error}</p> : null}
    </div> : null}
  </div>;
}
