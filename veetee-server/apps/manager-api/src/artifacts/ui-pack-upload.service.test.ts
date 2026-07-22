import { generateKeyPairSync } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Readable } from "node:stream";

import { afterEach, describe, expect, it, vi } from "vitest";

import { buildUiPack } from "../../../../scripts/lib/ui-pack.mjs";
import type { Principal } from "../auth/auth.types.js";
import { ArtifactFilesService } from "./artifact-files.service.js";
import type { ResourceCatalogService } from "./resource-catalog.service.js";
import { ResourceManifestService } from "./resource-manifest.service.js";
import { UiPackUploadService } from "./ui-pack-upload.service.js";

const principal: Principal = {
  userId: "10000000-0000-4000-8000-000000000001",
  tenantId: "20000000-0000-4000-8000-000000000001",
  tenantSlug: "test",
  role: "ADMIN",
  email: "admin@example.test",
  displayName: "Test Admin",
};

describe("UiPackUploadService", () => {
  let root = "";

  afterEach(async () => {
    vi.unstubAllEnvs();
    if (root) await rm(root, { recursive: true, force: true });
    root = "";
  });

  it("streams, validates, signs and catalogs a UI Pack", async () => {
    root = await mkdtemp(join(tmpdir(), "veetee-ui-upload-"));
    const artifactRoot = join(root, "artifacts");
    const privateKeyPath = join(root, "release.pem");
    const { privateKey, publicKey } = generateKeyPairSync("ed25519");
    await writeFile(
      privateKeyPath,
      privateKey.export({ format: "pem", type: "pkcs8" }),
      { mode: 0o600 },
    );
    const publicDer = publicKey.export({ format: "der", type: "spki" });
    const publicHex = publicDer.subarray(publicDer.length - 32).toString("hex");
    vi.stubEnv("VEETEE_ARTIFACT_ROOT", artifactRoot);
    vi.stubEnv("VEETEE_MANAGER_PUBLIC_URL", "http://192.168.1.20:8001/");
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_PRIVATE_KEY", privateKeyPath);
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_KEY_ID", "test-ui-release");
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX", publicHex);
    vi.stubEnv("VEETEE_RESOURCE_SECURITY_EPOCH", "3");

    const files = new ArtifactFilesService();
    const manifests = new ResourceManifestService(files);
    const registerArtifact = vi.fn(async (id: string, license: string) => {
      const validated = await manifests.validate(id);
      return {
        id,
        kind: validated.kind,
        version: validated.version,
        channel: validated.channel,
        sizeBytes: validated.sizeBytes,
        sha256: validated.sha256,
        contentType: validated.contentType,
        runtime: validated.runtime,
        runtimeAbi: validated.runtimeAbi,
        license,
        board: validated.board,
        minFirmware: validated.minFirmware,
        maxFirmware: validated.maxFirmware,
        signatureKeyId: validated.signatureKeyId,
        securityEpoch: validated.securityEpoch,
        benchmarkStatus: "not_run" as const,
        status: "validated" as const,
        createdAt: new Date(0).toISOString(),
      };
    });
    const catalog = { registerArtifact } as unknown as ResourceCatalogService;
    const service = new UiPackUploadService(catalog);
    const built = await buildUiPack(
      resolve(process.cwd(), "../../ui-packs/signal"),
    );

    const artifact = await service.stage(
      Readable.from([built.buffer]),
      "signal.vtp",
      { principal, requestId: "ui-upload-test" },
    );

    expect(artifact).toMatchObject({
      id: "ui-signal-1.0.0",
      kind: "display_assets",
      runtime: "veetee-ui",
      runtimeAbi: 1,
      status: "validated",
      securityEpoch: 3,
    });
    expect(registerArtifact).toHaveBeenCalledTimes(1);
    await expect(manifests.validate(artifact.id)).resolves.toMatchObject({
      kind: "display_assets",
      version: "1.0.0",
    });
  });

  it("rejects a malformed pack before catalog registration", async () => {
    root = await mkdtemp(join(tmpdir(), "veetee-ui-upload-"));
    vi.stubEnv("VEETEE_ARTIFACT_ROOT", join(root, "artifacts"));
    const registerArtifact = vi.fn();
    const service = new UiPackUploadService({ registerArtifact } as unknown as ResourceCatalogService);
    await expect(
      service.stage(Readable.from([Buffer.from("not-a-pack")]), "broken.vtp", {
        principal,
        requestId: "ui-upload-invalid",
      }),
    ).rejects.toThrow(/magic|header|size|unexpectedly/i);
    expect(registerArtifact).not.toHaveBeenCalled();
  });
});
