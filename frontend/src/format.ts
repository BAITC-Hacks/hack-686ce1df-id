import type { Role } from './types/api';

export const ROLE_LABELS: Record<Role, string> = {
  consolidator: 'Консолидатор',
  transit: 'Транзитный',
  distributor: 'Распределитель',
  terminal: 'Терминальный',
  coordinator: 'Координатор',
  peripheral: 'Периферийный',
};

export const ROLE_COLORS: Record<Role, string> = {
  consolidator: '#5278ae',
  transit: '#44989c',
  distributor: '#7c75b9',
  terminal: '#c28e42',
  coordinator: '#47806c',
  peripheral: '#84909f',
};

const numberFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const NO_DATA = 'нет данных';

/** Format decimal text directly, preserving every digit and the original scale. */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return NO_DATA;
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const [, sign, integer, fraction] = match;
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0');
  return `${sign}${grouped}${fraction === undefined ? '' : `,${fraction}`}\u00a0₸`;
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? NO_DATA
    : numberFormatter.format(value);
}

export function formatValue(value: number | string | boolean | null | undefined): string {
  if (value === null || value === undefined) return NO_DATA;
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (typeof value === 'number') return formatNumber(value);
  return value;
}
