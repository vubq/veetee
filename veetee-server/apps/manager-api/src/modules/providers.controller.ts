import { Body, Controller, Get, Param, Post } from "@nestjs/common";
import { IsBoolean, IsIn, IsOptional, IsString, IsUrl, Length } from "class-validator";

import { ControlPlaneStore, type ProviderRecord } from "../store/control-plane.store.js";

class CreateProviderDto {
  @IsIn(["vad", "asr", "llm", "tts", "realtime"])
  kind!: ProviderRecord["kind"];

  @IsString()
  @Length(1, 120)
  adapter!: string;

  @IsString()
  @Length(1, 200)
  model!: string;

  @IsOptional()
  @IsUrl({ require_tld: false })
  baseUrl?: string;

  @IsBoolean()
  secretConfigured!: boolean;

  @IsBoolean()
  enabled!: boolean;
}

@Controller("api/providers")
export class ProvidersController {
  constructor(private readonly store: ControlPlaneStore) {}

  @Get()
  list(): ProviderRecord[] {
    return this.store.listProviders();
  }

  @Post()
  create(@Body() input: CreateProviderDto): ProviderRecord {
    return this.store.createProvider(input);
  }

  @Post(":id/test")
  test(@Param("id") id: string): ProviderRecord {
    return this.store.testProvider(id);
  }
}
