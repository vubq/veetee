export type ManagerPage =
  | "overview"
  | "devices"
  | "agents"
  | "providers"
  | "lab"
  | "resources"
  | "operations";

export interface AgentDraftInput {
  id: string;
  name: string;
  defaultLocale: string;
  interactionMode: "auto" | "manual" | "realtime";
  persona: string;
  draftConfig: Record<string, unknown>;
}

export interface ProviderUpdateInput {
  adapter: string;
  model: string;
  baseUrl: string | null;
  enabled: boolean;
  priority: number;
  locales: string[];
  secretAction: "keep" | "rotate" | "clear";
  secret?: string;
}

export interface ToastItem {
  id: number;
  message: string;
  tone: "success" | "danger" | "info";
}
