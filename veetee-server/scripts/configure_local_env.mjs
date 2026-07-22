import { randomBytes } from "node:crypto";
import { chmod, mkdir, stat, writeFile } from "node:fs/promises";
import { networkInterfaces } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const serverRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const environmentPath = resolve(serverRoot, "apps/manager-api/.env");
const credentialsPath = resolve(serverRoot, "data/local-admin.txt");

function secret(bytes = 36) {
  return randomBytes(bytes).toString("base64url");
}

function lanAddress() {
  for (const entries of Object.values(networkInterfaces())) {
    for (const entry of entries ?? []) {
      if (entry.family === "IPv4" && !entry.internal && !entry.address.startsWith("169.254.")) {
        return entry.address;
      }
    }
  }
  return "127.0.0.1";
}

async function assertMissing(path) {
  try {
    await stat(path);
    throw new Error(`Refusing to overwrite existing local configuration: ${path}`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

async function main() {
  await assertMissing(environmentPath);
  await assertMissing(credentialsPath);
  const address = lanAddress();
  const adminPassword = `Veetee-${secret(18)}`;
  const artifactRoot = resolve(serverRoot, "data/artifacts");
  const lines = [
    "DATABASE_URL=postgresql://veetee@127.0.0.1:5432/veetee?schema=public",
    "VEETEE_INTEGRATION_DATABASE_URL=postgresql://veetee@127.0.0.1:5432/veetee_test?schema=public",
    "REDIS_URL=redis://127.0.0.1:6379/1",
    "VEETEE_MANAGER_PORT=8001",
    "VEETEE_MANAGER_HOST=0.0.0.0",
    `VEETEE_MANAGER_CORS_ORIGIN=http://127.0.0.1:8081,http://${address}:8081`,
    `VEETEE_MANAGER_PUBLIC_URL=http://${address}:8001`,
    `VEETEE_VOICE_WS_URL=ws://${address}:8000/veetee/v1/`,
    `VEETEE_VOICE_LAB_WS_URL=ws://${address}:8000/veetee/lab/v1/`,
    "VEETEE_FIRMWARE_VERSION=0.2.0",
    "VEETEE_FIRMWARE_URL=",
    "VEETEE_RESOURCE_VERSION=1.0.0",
    `VEETEE_RESOURCE_MANIFEST_URL=http://${address}:8001/veetee/artifacts/manifests/stable`,
    "VEETEE_RESOURCE_MANIFEST_ID=stable",
    `VEETEE_ARTIFACT_ROOT=${artifactRoot}`,
    "VEETEE_MAX_RESOURCE_ARTIFACT_BYTES=67108864",
    "VEETEE_ACTIVATION_MESSAGE_TEMPLATE=Nhap ma {code} trong Veetee Manager",
    `VEETEE_AUTH_SECRET=${secret()}`,
    `VEETEE_LAB_TOKEN_SECRET=${secret()}`,
    `VEETEE_DEVICE_TOKEN_SECRET=${secret()}`,
    `VEETEE_MASTER_KEY=${randomBytes(32).toString("base64")}`,
    `VEETEE_INTERNAL_SERVICE_TOKEN=${secret()}`,
    "VEETEE_BOOTSTRAP_TENANT_SLUG=veetee-local",
    "VEETEE_BOOTSTRAP_TENANT_NAME=Veetee Local",
    "VEETEE_BOOTSTRAP_ADMIN_EMAIL=owner@veetee.local",
    `VEETEE_BOOTSTRAP_ADMIN_PASSWORD=${adminPassword}`,
    "VEETEE_BOOTSTRAP_ADMIN_NAME=Veetee Owner",
    "",
  ];
  await mkdir(dirname(credentialsPath), { recursive: true });
  await writeFile(environmentPath, lines.join("\n"), { flag: "wx", mode: 0o600 });
  await writeFile(
    credentialsPath,
    `Manager URL: http://${address}:8001\nEmail: owner@veetee.local\nPassword: ${adminPassword}\n`,
    { flag: "wx", mode: 0o600 },
  );
  await chmod(environmentPath, 0o600);
  await chmod(credentialsPath, 0o600);
  process.stdout.write(
    `Created local Manager configuration for ${address}; credentials are stored in ignored data/local-admin.txt\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
