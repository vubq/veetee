import "reflect-metadata";

import cookie from "@fastify/cookie";
import cors from "@fastify/cors";
import { ValidationPipe } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import { FastifyAdapter, type NestFastifyApplication } from "@nestjs/platform-fastify";
import { LogController } from "fastify";

import { AppModule } from "./app.module.js";
import { HttpExceptionFilter } from "./common/http-exception.filter.js";

class VeeteeLogController extends LogController {
  constructor() {
    super({ disableRequestLogging: true });
  }
}

async function bootstrap(): Promise<void> {
  const adapter = new FastifyAdapter({
    logger: {
      redact: ["req.headers.authorization", "req.headers.cookie", "res.headers.set-cookie"],
    },
    logController: new VeeteeLogController(),
    trustProxy: true,
  });
  const app = await NestFactory.create<NestFastifyApplication>(AppModule, adapter);
  await app.register(cookie);
  await app.register(cors, {
    origin: process.env.VEETEE_MANAGER_CORS_ORIGIN?.split(",") ?? ["http://127.0.0.1:8081"],
    credentials: true,
  });
  app.useGlobalPipes(
    new ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true }),
  );
  app.useGlobalFilters(new HttpExceptionFilter());
  app.enableShutdownHooks();
  await app.listen(
    Number(process.env.VEETEE_MANAGER_PORT ?? 8001),
    process.env.VEETEE_MANAGER_HOST ?? "127.0.0.1",
  );
}

void bootstrap();
