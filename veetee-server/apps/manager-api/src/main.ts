import "reflect-metadata";

import cors from "@fastify/cors";
import { ValidationPipe } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import { FastifyAdapter, type NestFastifyApplication } from "@nestjs/platform-fastify";

import { AppModule } from "./app.module.js";

async function bootstrap(): Promise<void> {
  const adapter = new FastifyAdapter({ logger: true, trustProxy: true });
  const app = await NestFactory.create<NestFastifyApplication>(AppModule, adapter);
  await app.register(cors, {
    origin: process.env.VEETEE_MANAGER_CORS_ORIGIN?.split(",") ?? true,
    credentials: true,
  });
  app.useGlobalPipes(
    new ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true }),
  );
  app.enableShutdownHooks();
  await app.listen(Number(process.env.VEETEE_MANAGER_PORT ?? 8001), "127.0.0.1");
}

void bootstrap();
