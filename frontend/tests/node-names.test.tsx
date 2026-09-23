// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { searchNames } from '../src/names';
import { NodePicker } from '../src/components/NodePicker';
import { SearchPanel } from '../src/components/SearchPanel';
import { NodeDetails } from '../src/components/NodeDetails';
import { demoInfo, makeNode } from './helpers';

afterEach(cleanup);
const nodes = demoInfo().nodes;
it('distinguishes namesakes, prioritizes exact gid, and preserves zeroes', () => {
  expect(searchNames(nodes, 'ивАН')).toHaveLength(2);
  expect(searchNames(nodes, '7').map(node => node.gid)).toEqual(['7', '0007']);
  const onChange = vi.fn();
  render(<NodePicker label="Получатель" nodes={nodes} value="" onChange={onChange} disabled={false} />);
  fireEvent.change(screen.getByRole('combobox', { name: 'Получатель' }), { target: { value: 'Иванов' } });
  fireEvent.click(screen.getByRole('option', { name: 'Александр Иванов · 0007' }));
  expect(onChange).toHaveBeenCalledWith('0007');
});
it('requires an explicit choice for duplicate names and searches beyond priorities', () => {
  const onSearch = vi.fn();
  render(<SearchPanel onSearch={onSearch} disabled={false} nodes={nodes} />);
  const search = screen.getByRole('textbox', { name: 'Найти узел' });
  fireEvent.change(search, { target: { value: 'Александр Иванов' } });
  fireEvent.submit(search.closest('form')!);
  expect(onSearch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Александр Иванов ID: 7' }));
  expect(onSearch).toHaveBeenLastCalledWith('7');
  fireEvent.change(search, { target: { value: 'Садыкова' } });
  fireEvent.submit(search.closest('form')!);
  expect(onSearch).toHaveBeenLastCalledWith('outside-top');
});
it('supports keyboard picker choice without choosing the first namesake implicitly', () => {
  const onChange = vi.fn();
  render(<NodePicker label="Получатель" nodes={nodes} value="" onChange={onChange} disabled={false} />);
  const input = screen.getByRole('combobox');
  fireEvent.change(input, { target: { value: 'Иванов' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onChange).toHaveBeenCalledWith('0007');
});
it('renders names as text, including markup-like input', () => {
  const { container } = render(<NodeDetails node={makeNode('0007')} displayName="<img src=x>" onSelectCluster={vi.fn()} onSelectComponent={vi.fn()} />);
  expect(screen.getByRole('heading', { name: '<img src=x>' })).toBeTruthy();
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('ID: 0007')).toBeTruthy();
});
