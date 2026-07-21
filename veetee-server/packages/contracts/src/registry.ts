import Ajv2020Module from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormatsModule from "ajv-formats";

import { schemas } from "./schemas.js";

export type SchemaId = (typeof schemas)[number]["$id"];

export interface ValidationResult<T = unknown> {
  valid: boolean;
  data?: T;
  errors: ErrorObject[];
}

type AjvInstance = {
  addSchema(schema: unknown): void;
  getSchema(schemaId: string): ValidateFunction | undefined;
};
type AjvConstructor = new (options: Record<string, unknown>) => AjvInstance;
const Ajv2020 = Ajv2020Module as unknown as AjvConstructor;
const addFormats = addFormatsModule as unknown as (ajv: AjvInstance) => void;

export class ContractRegistry {
  private readonly ajv: AjvInstance;

  public constructor() {
    this.ajv = new Ajv2020({ allErrors: true, strict: true });
    addFormats(this.ajv);
    for (const schema of schemas) {
      this.ajv.addSchema(schema);
    }
  }

  public validator(schemaId: SchemaId): ValidateFunction {
    const validate = this.ajv.getSchema(schemaId);
    if (!validate) {
      throw new Error(`Unknown contract schema: ${schemaId}`);
    }
    return validate;
  }

  public validate<T = unknown>(schemaId: SchemaId, data: unknown): ValidationResult<T> {
    const validate = this.validator(schemaId);
    const valid = validate(data);
    return {
      valid,
      ...(valid ? { data: data as T } : {}),
      errors: validate.errors ? [...validate.errors] : [],
    };
  }
}

export const fixtureSchemaIds = {
  "artifacts/device-capability-v1.json": "https://schemas.veetee.local/artifacts/device-capability-v1.json",
  "artifacts/resource-manifest-v1.json": "https://schemas.veetee.local/artifacts/resource-manifest-v1.json",
  "artifacts/signed-resource-manifest-vector-v1.json": "https://schemas.veetee.local/artifacts/signed-manifest-vector-v1.json",
  "config/agent-conversation-policy-v1.json": "https://schemas.veetee.local/config/agent-conversation-policy-v1.json",
  "config/provider-baseline-v1.json": "https://schemas.veetee.local/config/provider-baseline-v1.json",
  "mcp/initialize.json": "https://schemas.veetee.local/mcp/envelope-v1.json",
  "mcp/tools-call-volume.json": "https://schemas.veetee.local/mcp/envelope-v1.json",
  "ota/bootstrap-bound.json": "https://schemas.veetee.local/ota/bootstrap-v1.json",
  "ota/bootstrap-unbound.json": "https://schemas.veetee.local/ota/bootstrap-v1.json",
  "ws/abort-interrupt-profile.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/abort-speaking.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/device-hello-v1.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/listen-detect-wake-word.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/listen-start-auto.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/listen-start-button-auto.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/listen-start-manual.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/listen-start-wake-auto.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/server-hello-v1.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/system-assistant-sleep-timeout.json": "https://schemas.veetee.local/ws/control-event-v1.json",
  "ws/system-config-changed.json": "https://schemas.veetee.local/ws/control-event-v1.json",
} as const satisfies Record<string, SchemaId>;
