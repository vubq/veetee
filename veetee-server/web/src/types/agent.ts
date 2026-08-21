export interface AgentSummary {
  id: string
  name: string
  role: string
  model: string
  lastConversation: string
  deviceCount: number
  online: boolean
  version?: number
  rolePrompt?: string
  personality?: string
  addressStyle?: string
  language?: string
  detailLevel?: string
  responseStyle?: string
  modelId?: string
  voiceId?: string
  intentStrategy?: string
  memoryEnabled?: boolean
  memoryMinConfidence?: number
  toolPolicy?: Record<string, unknown>
  memoryPolicy?: Record<string, unknown>
}
