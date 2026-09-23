import { requestJson } from './client';
import type { AddNodeCommand, AddTransferCommand, DemoInfo, MutationResponse } from '../types/demo';
export const demoApi = {
  info: (signal?: AbortSignal) => requestJson<DemoInfo>('/demo', { signal, allowNullRunId: true }),
  addNode: (body: AddNodeCommand, signal?: AbortSignal) => requestJson<MutationResponse>('/demo/nodes', { body, signal, timeoutMs: 45_000 }),
  addTransfer: (body: AddTransferCommand, signal?: AbortSignal) => requestJson<MutationResponse>('/demo/transfers', { body, signal, timeoutMs: 45_000 }),
};
