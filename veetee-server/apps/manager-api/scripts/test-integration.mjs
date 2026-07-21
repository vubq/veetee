import { spawn } from "node:child_process";

const integrationUrl = process.env.VEETEE_INTEGRATION_DATABASE_URL;
const developmentUrl = process.env.DATABASE_URL;
if (!integrationUrl) {
  throw new Error("VEETEE_INTEGRATION_DATABASE_URL is required");
}
const integrationDatabase = new URL(integrationUrl).pathname.replace(/^\//, "");
const developmentDatabase = developmentUrl
  ? new URL(developmentUrl).pathname.replace(/^\//, "")
  : "";
if (!integrationDatabase.endsWith("_test") || integrationDatabase === developmentDatabase) {
  throw new Error("Integration tests require a dedicated *_test database");
}

function run(command, args, extraEnvironment = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      env: { ...process.env, ...extraEnvironment },
    });
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited with code ${code ?? "unknown"}`));
    });
  });
}

const environment = { DATABASE_URL: integrationUrl, VEETEE_INTEGRATION: "1" };
await run("../../node_modules/.bin/prisma", ["migrate", "deploy"], environment);
await run(
  process.execPath,
  ["../../node_modules/vitest/vitest.mjs", "run", "src/store/control-plane.integration.test.ts"],
  environment,
);
