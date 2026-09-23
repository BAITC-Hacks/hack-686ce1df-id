export type NodeName = { gid: string; display_name: string };
export type DemoGroup = { id: string; name: string };
export type DemoLimits = { date_from: string; date_to: string; min_amount_kzt: string; max_amount_kzt: string };
export type DemoInfo = {
  contract_version: '1.0'; run_id: string | null; enabled: boolean;
  node_count: number; transfer_count: number | null; nodes: NodeName[];
  groups: DemoGroup[]; limits: DemoLimits;
};
export type DemoQuality = { is_seed?: boolean | null; hop_depth?: number | null; outbound_censored?: boolean | null; inbound_incomplete?: boolean | null };
export type NodeDraft = { display_name: string; gid?: string; group?: null | { kind: 'existing'; id: string } | { kind: 'new'; name: string }; quality?: DemoQuality };
export type TransferDraft = { source: string; target: string; amount_kzt: string; date: string };
export type CommandVersion = { run_id: string; request_id: string };
export type AddNodeCommand = NodeDraft & CommandVersion;
export type AddTransferCommand = TransferDraft & CommandVersion;
export type DemoMutation = { kind: 'node'; command: AddNodeCommand } | { kind: 'transfer'; command: AddTransferCommand };
export type MutationResponse = { contract_version: '1.0'; run_id: string; created_id: string; select_gid: string; node_count: number; transfer_count: number };
export const DEMO_LIMITS: DemoLimits = { date_from: '2026-07-01', date_to: '2026-07-31', min_amount_kzt: '5000.00', max_amount_kzt: '1000000000000.00' };
export function disabledDemo(runId: string | null): DemoInfo {
  return { contract_version: '1.0', run_id: runId, enabled: false, node_count: 0, transfer_count: null, nodes: [], groups: [], limits: DEMO_LIMITS };
}
