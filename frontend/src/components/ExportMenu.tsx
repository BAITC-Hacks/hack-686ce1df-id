import { useEffect, useRef, useState } from 'react';
import { Download, ChevronDown, LoaderCircle } from 'lucide-react';
import { api } from '../api/client';

type ExportFile = readonly [name: string, label: string, extension: 'JSON' | 'CSV', filename?: string];

const files: readonly ExportFile[] = [
  ['nodes', 'Узлы', 'JSON'], ['edges', 'Связи', 'JSON'], ['clusters', 'Кластеры', 'JSON'],
  ['priorities', 'Приоритеты', 'CSV'], ['roles', 'Роли', 'CSV'], ['rules', 'Правила', 'JSON'],
  ['audit', 'Аудит данных', 'JSON'], ['manifest', 'Паспорт расчёта', 'JSON'],
] as const;

const submissionFiles: readonly ExportFile[] = [
  ['nodes_roles', 'Роли узлов · CSV задания', 'CSV'],
  ['clusters_csv', 'Кластеры · CSV задания', 'CSV', 'clusters.csv'],
  ['top_nodes', 'Приоритетные узлы · CSV задания', 'CSV'],
];

const demoFiles: readonly ExportFile[] = [
  ['node_names', 'Имена узлов', 'JSON'], ['source', 'Исходные демоданные', 'JSON'], ['transfers', 'Отдельные переводы', 'JSON'],
];

export function ExportMenu({ runId, onRunConflict, demo = false, realRun = false }: { runId: string | null; onRunConflict: () => void; demo?: boolean; realRun?: boolean }) {
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
  const download = async (name: string, filename: string) => {
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
      link.download = filename;
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
      {[...files, ...(realRun && !demo ? submissionFiles : []), ...(demo ? demoFiles : [])].map(([name, label, extension, filename = `${name}.${extension.toLowerCase()}`]) => <button key={name} aria-label={`Скачать ${filename}`} onClick={() => void download(name, filename)} disabled={busy !== null}>{busy === name ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />}<span>{label}</span><small>{extension}</small></button>)}
      {error ? <p className="error-text" role="alert">{error}</p> : null}
    </div> : null}
  </div>;
}
