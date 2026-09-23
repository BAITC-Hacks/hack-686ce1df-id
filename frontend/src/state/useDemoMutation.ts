import { useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { demoApi } from '../api/demo';
import type { DemoMutation, MutationResponse, NodeDraft, TransferDraft } from '../types/demo';

type MutationState = { pending: boolean; error: string | null; uncertain: boolean; conflict: boolean; saved: MutationResponse | null };
const initialState: MutationState = { pending: false, error: null, uncertain: false, conflict: false, saved: null };

/** Keep the command itself until its outcome is known, including across panel closure. */
export function useDemoMutation(onSaved: (gid: string) => Promise<boolean>) {
  const [state, setState] = useState<MutationState>(initialState);
  const command = useRef<DemoMutation | null>(null);
  const receipt = useRef<MutationResponse | null>(null);
  const busy = useRef(false);
  const conflict = useRef(false);

  const synchronize = async (response: MutationResponse): Promise<boolean> => {
    try {
      if (await onSaved(response.select_gid)) return true;
    } catch { /* The write is confirmed; a failed read must never cause another write. */ }
    setState(previous => ({ ...previous, error: 'Сохранено. Не удалось обновить данные; повторите только обновление.' }));
    return false;
  };

  const execute = async (operation: DemoMutation): Promise<boolean> => {
    if (busy.current || receipt.current) return false;
    busy.current = true;
    setState({ pending: true, error: null, uncertain: false, conflict: false, saved: null });
    try {
      const response = operation.kind === 'node'
        ? await demoApi.addNode(operation.command) : await demoApi.addTransfer(operation.command);
      receipt.current = response;
      command.current = null;
      setState({ pending: true, error: null, uncertain: false, conflict: false, saved: response });
      return await synchronize(response);
    } catch (cause) {
      const stale = cause instanceof ApiError && cause.code === 'stale_run';
      const uncertain = !(cause instanceof ApiError)
        || cause.status === 0 || cause.code === 'INVALID_RESPONSE' || cause.code === 'CONTRACT_MISMATCH'
        || (cause.status >= 500 && cause.code !== 'demo_write_failed');
      if (!uncertain) command.current = null;
      conflict.current = stale;
      setState({ pending: false, saved: null, conflict: stale, uncertain,
        error: stale ? 'Данные изменились в другом окне. Обновите данные, затем сохраните этот черновик снова.'
          : cause instanceof Error ? cause.message : 'Не удалось подтвердить сохранение.' });
      return false;
    } finally {
      busy.current = false;
      setState(previous => ({ ...previous, pending: false }));
    }
  };

  const submit = (kind: 'node' | 'transfer', draft: NodeDraft | TransferDraft, runId: string): Promise<boolean> => {
    if (busy.current || command.current || receipt.current || conflict.current) return Promise.resolve(false);
    const version = { run_id: runId, request_id: crypto.randomUUID() };
    const operation: DemoMutation = kind === 'node'
      ? { kind, command: { ...(draft as NodeDraft), ...version } }
      : { kind, command: { ...(draft as TransferDraft), ...version } };
    command.current = operation;
    return execute(operation);
  };
  const retry = () => command.current ? execute(command.current) : Promise.resolve(false);
  const retryRefresh = async (): Promise<boolean> => {
    if (busy.current || !receipt.current) return false;
    busy.current = true;
    setState(previous => ({ ...previous, pending: true, error: null }));
    try { return await synchronize(receipt.current); }
    finally { busy.current = false; setState(previous => ({ ...previous, pending: false })); }
  };
  const acknowledgeConflict = () => {
    conflict.current = false;
    setState(previous => ({ ...previous, conflict: false, error: null }));
  };
  const reset = () => {
    if (busy.current || command.current) return;
    receipt.current = null;
    conflict.current = false;
    setState(initialState);
  };
  return { ...state, submit, retry, retryRefresh, acknowledgeConflict, reset };
}
