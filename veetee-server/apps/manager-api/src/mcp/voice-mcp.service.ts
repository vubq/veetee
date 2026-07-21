import {
  BadGatewayException,
  ConflictException,
  GatewayTimeoutException,
  Injectable,
  NotFoundException,
  ServiceUnavailableException,
  UnprocessableEntityException,
} from "@nestjs/common";

export interface McpToolRecord {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  audience: "regular" | "user";
  safetyClass: "read_only" | "reversible" | "disruptive" | "destructive";
  requiresConfirmation: boolean;
}

@Injectable()
export class VoiceMcpService {
  async listTools(deviceId: string): Promise<McpToolRecord[]> {
    const response = await this.request(
      `/internal/v1/devices/${encodeURIComponent(deviceId)}/mcp/tools`,
      { method: "GET" },
      10_000,
    );
    const payload = await this.parseJson(response);
    if (!Array.isArray(payload) || payload.length > 128) {
      throw new BadGatewayException("Voice MCP catalog is invalid");
    }
    return payload.map((item) => this.parseTool(item));
  }

  async callTool(
    deviceId: string,
    name: string,
    argumentsValue: Record<string, unknown>,
    confirmed: boolean,
    timeoutSeconds: number,
  ): Promise<Record<string, unknown>> {
    const response = await this.request(
      `/internal/v1/devices/${encodeURIComponent(deviceId)}/mcp/tools/${encodeURIComponent(name)}/call`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          arguments: argumentsValue,
          confirmed,
          timeout_seconds: timeoutSeconds,
        }),
      },
      Math.ceil(timeoutSeconds * 1_000) + 2_000,
    );
    const payload = await this.parseJson(response);
    if (!this.isRecord(payload)) {
      throw new BadGatewayException("Voice MCP result is invalid");
    }
    return payload;
  }

  private async request(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
    const baseUrl = process.env.VEETEE_VOICE_INTERNAL_URL ?? "http://127.0.0.1:8000";
    const token = process.env.VEETEE_INTERNAL_SERVICE_TOKEN ?? "";
    this.validateBaseUrl(baseUrl);
    if (!token) throw new ServiceUnavailableException("Voice internal token is not configured");
    let response: Response;
    try {
      const headers = new Headers(init.headers);
      headers.set("authorization", `Bearer ${token}`);
      response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
        ...init,
        headers,
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      if (error instanceof Error && error.name === "TimeoutError") {
        throw new GatewayTimeoutException("Voice MCP request timed out");
      }
      throw new ServiceUnavailableException("Voice server is unavailable");
    }
    if (response.ok) return response;
    if (response.status === 404) throw new NotFoundException("Device MCP tool was not found");
    if (response.status === 409) throw new ConflictException("Device MCP action needs attention");
    if (response.status === 422) throw new UnprocessableEntityException("Device MCP call was rejected");
    if (response.status === 504) {
      throw new GatewayTimeoutException("Device MCP operation timed out");
    }
    if (response.status === 502) {
      throw new BadGatewayException("Device MCP operation failed");
    }
    throw new ServiceUnavailableException("Voice MCP gateway is unavailable");
  }

  private parseTool(value: unknown): McpToolRecord {
    if (!this.isRecord(value)) throw new BadGatewayException("Voice MCP tool is invalid");
    const { name, description, inputSchema, audience, safetyClass, requiresConfirmation } = value;
    if (
      typeof name !== "string" ||
      !name.startsWith("self.") ||
      name.length > 128 ||
      typeof description !== "string" ||
      description.length > 512 ||
      !this.isRecord(inputSchema) ||
      !["regular", "user"].includes(String(audience)) ||
      !["read_only", "reversible", "disruptive", "destructive"].includes(
        String(safetyClass),
      ) ||
      typeof requiresConfirmation !== "boolean" ||
      (audience === "user" && !requiresConfirmation)
    ) {
      throw new BadGatewayException("Voice MCP tool is invalid");
    }
    return {
      name,
      description,
      inputSchema,
      audience: audience as McpToolRecord["audience"],
      safetyClass: safetyClass as McpToolRecord["safetyClass"],
      requiresConfirmation,
    };
  }

  private validateBaseUrl(value: string): void {
    let url: URL;
    try {
      url = new URL(value);
    } catch {
      throw new ServiceUnavailableException("Voice internal URL is invalid");
    }
    const loopback = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
    if (
      (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) ||
      url.username !== "" ||
      url.password !== "" ||
      url.search !== "" ||
      url.hash !== "" ||
      !["", "/"].includes(url.pathname)
    ) {
      throw new ServiceUnavailableException("Voice internal URL must use HTTPS or loopback HTTP");
    }
  }

  private async parseJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      throw new BadGatewayException("Voice MCP response is not valid JSON");
    }
  }

  private isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }
}
