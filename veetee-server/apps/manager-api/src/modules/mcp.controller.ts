import { Controller, Get } from "@nestjs/common";

@Controller("api/v1/mcp")
export class McpController {
  @Get("tools")
  list(): Array<Record<string, unknown>> {
    return [
      {
        name: "self.get_device_status",
        description: "Read the current device status.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        audience: "regular",
      },
      {
        name: "self.audio_speaker.set_volume",
        description: "Set speaker volume from 0 to 100.",
        inputSchema: {
          type: "object",
          properties: { volume: { type: "integer", minimum: 0, maximum: 100 } },
          required: ["volume"],
          additionalProperties: false,
        },
        audience: "regular",
      },
    ];
  }
}
