import type { NodeName } from './types/demo';
export const EMPTY_NAMES: ReadonlyMap<string, string> = new Map();
export function searchNames(nodes: readonly NodeName[], query: string): NodeName[] {
  const needle = query.trim().toLocaleLowerCase('ru');
  if (!needle) return [];
  return nodes.filter(node => node.gid.toLocaleLowerCase('ru').includes(needle) || node.display_name.toLocaleLowerCase('ru').includes(needle))
    .sort((a, b) => Number(b.gid === query.trim()) - Number(a.gid === query.trim()) || a.display_name.localeCompare(b.display_name, 'ru') || a.gid.localeCompare(b.gid, 'en'));
}
export function nodeLabel(gid: string, names: ReadonlyMap<string, string>): string {
  const name = names.get(gid);
  return name ? `${name} · ${gid}` : gid;
}
