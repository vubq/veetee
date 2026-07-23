import cors from "@fastify/cors";
import Fastify from "fastify";
import { afterEach, describe, expect, it } from "vitest";

import { createManagerCorsOptions } from "./cors-options.js";

const applications: ReturnType<typeof Fastify>[] = [];

afterEach(async () => {
  await Promise.all(applications.splice(0).map((application) => application.close()));
});

describe("Manager API CORS", () => {
  it.each(["PATCH", "PUT"])("allows %s mutations from a configured Manager Web origin", async (method) => {
    const application = Fastify();
    applications.push(application);
    await application.register(
      cors,
      createManagerCorsOptions(" http://127.0.0.1:8081, https://veetee.example.test "),
    );

    const response = await application.inject({
      method: "OPTIONS",
      url: "/api/v1/example",
      headers: {
        origin: "http://127.0.0.1:8081",
        "access-control-request-method": method,
        "access-control-request-headers": "authorization,content-type",
      },
    });

    expect(response.statusCode).toBe(204);
    expect(response.headers["access-control-allow-origin"]).toBe("http://127.0.0.1:8081");
    expect(response.headers["access-control-allow-credentials"]).toBe("true");
    expect(
      response.headers["access-control-allow-methods"]?.split(",").map((value) => value.trim()),
    ).toContain(method);
  });
});
