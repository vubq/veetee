import type { FastifyCorsOptions } from "@fastify/cors";

const managerCorsMethods = ["GET", "HEAD", "POST", "PUT", "PATCH", "OPTIONS"];
const defaultManagerOrigin = "http://127.0.0.1:8081";

export function createManagerCorsOptions(rawOrigins?: string): FastifyCorsOptions {
  const origins = (rawOrigins ?? defaultManagerOrigin)
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);

  return {
    origin: origins.length ? origins : [defaultManagerOrigin],
    credentials: true,
    methods: managerCorsMethods,
  };
}
