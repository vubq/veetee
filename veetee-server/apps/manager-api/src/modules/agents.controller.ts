import { Body, Controller, Get, Param, Post } from "@nestjs/common";
import { IsIn, IsLocale, IsString, Length } from "class-validator";

import { ControlPlaneStore, type AgentRecord } from "../store/control-plane.store.js";

class CreateAgentDto {
  @IsString()
  @Length(1, 80)
  name!: string;

  @IsLocale()
  defaultLocale!: string;

  @IsIn(["auto", "manual", "realtime"])
  interactionMode!: "auto" | "manual" | "realtime";

  @IsString()
  @Length(1, 20_000)
  persona!: string;
}

@Controller("api/agents")
export class AgentsController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get()
  list(): AgentRecord[] {
    return this.store.listAgents();
  }

  @Post()
  create(@Body() input: CreateAgentDto): AgentRecord {
    return this.store.createAgent(input);
  }

  @Post(":id/publish")
  publish(@Param("id") id: string): AgentRecord {
    return this.store.publishAgent(id);
  }
}
