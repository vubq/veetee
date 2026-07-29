import {
  createHash,
  generateKeyPairSync,
  verify,
  type KeyObject,
} from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { canonicalizeRestrictedJcs } from "../artifacts/resource-manifest.service.js";
import type { ControlPlaneStore } from "../store/control-plane.store.js";
import {
  DeviceConfigService,
  matchesDeviceConfigEtag,
} from "./device-config.service.js";

describe("DeviceConfigService", () => {
  let directory: string;
  let privateKeyPath: string;
  let publicKey: KeyObject;

  beforeEach(async () => {
    directory = await mkdtemp(join(tmpdir(), "veetee-device-config-"));
    privateKeyPath = join(directory, "release.pem");
    const pair = generateKeyPairSync("ed25519");
    publicKey = pair.publicKey;
    await writeFile(
      privateKeyPath,
      pair.privateKey.export({ format: "pem", type: "pkcs8" }),
      { mode: 0o600 },
    );
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_PRIVATE_KEY", privateKeyPath);
    vi.stubEnv("VEETEE_RESOURCE_SIGNING_KEY_ID", "test-device-config-release");
    vi.stubEnv("VEETEE_RESOURCE_SECURITY_EPOCH", "7");
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(directory, { recursive: true, force: true });
  });

  it("projects only bounded firmware fields and signs the restricted-JCS body", async () => {
    const store = {
      deviceConfigSource: vi.fn().mockResolvedValue({
        deviceId: "d74f1594-765c-4bd5-b07b-6443273777ed",
        version: 8,
        state: {
          agentId: "server-only-agent",
          agentConfigVersion: 42,
          resourceBundleVersion: "1.2.0",
          resourceManifestId: "server-only-manifest",
          wakeProfile: {
            schemaVersion: 1,
            id: "b80f676e-2fd2-47f9-8160-e68cf7e3a260",
            version: 4,
            name: "server-only-label",
            activationPhrase: "server-only-phrase",
            activation: {
              detectorId: "wn9s_hiesp",
              sensitivity: 0.64,
              cooldownMs: 1_200,
              allowedStates: ["standby"],
            },
            interrupt: null,
          },
        },
      }),
    } as unknown as ControlPlaneStore;

    const result = await new DeviceConfigService(store).snapshot(
      "d74f1594-765c-4bd5-b07b-6443273777ed",
    );

    expect(result.body).toMatchObject({
      schema_version: 1,
      device_id: "d74f1594-765c-4bd5-b07b-6443273777ed",
      version: 8,
      wake_profile: {
        id: "b80f676e-2fd2-47f9-8160-e68cf7e3a260",
        version: 4,
        required_resource_version: "1.2.0",
        activation: {
          model_id: "wn9s_hiesp",
          threshold_ppm: 640_000,
          cooldown_ms: 1_200,
        },
        interrupt: null,
        send_wake_audio: false,
      },
      signature: {
        algorithm: "ed25519",
        key_id: "test-device-config-release",
        security_epoch: 7,
      },
    });
    expect(JSON.stringify(result.body)).not.toMatch(
      /server-only|activationPhrase|allowedStates|agentConfigVersion/,
    );

    const unsigned = structuredClone(result.body);
    const signature = unsigned.signature.value;
    delete (unsigned.signature as Partial<typeof unsigned.signature>).value;
    const canonicalUnsigned = canonicalizeRestrictedJcs(unsigned);
    expect(
      verify(
        null,
        Buffer.from(canonicalUnsigned, "utf8"),
        publicKey,
        Buffer.from(signature, "base64"),
      ),
    ).toBe(true);
    expect(result.canonicalBody).toBe(canonicalizeRestrictedJcs(result.body));
    expect(result.etag).toBe(
      `cfg1-${createHash("sha256").update(result.canonicalBody).digest("base64url")}`,
    );
  });

  it("emits an explicitly null wake profile when no rollout is desired", async () => {
    const store = {
      deviceConfigSource: vi.fn().mockResolvedValue({
        deviceId: "d74f1594-765c-4bd5-b07b-6443273777ed",
        version: 1,
        state: { agentConfigVersion: 3 },
      }),
    } as unknown as ControlPlaneStore;

    const result = await new DeviceConfigService(store).snapshot(
      "d74f1594-765c-4bd5-b07b-6443273777ed",
    );

    expect(result.body.wake_profile).toBeNull();
    expect(result.body.version).toBe(1);
  });

  it("signs wake audio only from an explicit published privacy opt-in", async () => {
    const store = {
      deviceConfigSource: vi.fn().mockResolvedValue({
        deviceId: "d74f1594-765c-4bd5-b07b-6443273777ed",
        version: 9,
        state: {
          resourceBundleVersion: "1.2.0",
          wakeProfile: {
            id: "b80f676e-2fd2-47f9-8160-e68cf7e3a260",
            version: 5,
            sendWakeAudio: true,
            activation: {
              detectorId: "wn9s_hiesp",
              sensitivity: 0.64,
              cooldownMs: 1_200,
            },
            interrupt: null,
          },
        },
      }),
    } as unknown as ControlPlaneStore;

    const result = await new DeviceConfigService(store).snapshot(
      "d74f1594-765c-4bd5-b07b-6443273777ed",
    );

    expect(result.body.wake_profile?.send_wake_audio).toBe(true);
    const unsigned = structuredClone(result.body);
    const signature = unsigned.signature.value;
    delete (unsigned.signature as Partial<typeof unsigned.signature>).value;
    expect(verify(
      null,
      Buffer.from(canonicalizeRestrictedJcs(unsigned), "utf8"),
      publicKey,
      Buffer.from(signature, "base64"),
    )).toBe(true);
  });

  it("fails closed when a published profile is outside firmware safe bounds", async () => {
    const store = {
      deviceConfigSource: vi.fn().mockResolvedValue({
        deviceId: "d74f1594-765c-4bd5-b07b-6443273777ed",
        version: 2,
        state: {
          resourceBundleVersion: "1.0.0",
          wakeProfile: {
            id: "wake-unsafe",
            version: 1,
            activation: {
              detectorId: "wn9s_hiesp",
              sensitivity: 0.2,
              cooldownMs: 1_000,
            },
          },
        },
      }),
    } as unknown as ControlPlaneStore;

    await expect(new DeviceConfigService(store).snapshot(
      "d74f1594-765c-4bd5-b07b-6443273777ed",
    )).rejects.toThrow(/sensitivity/);
  });

  it("does not sign logical detector aliases as firmware model ids", async () => {
    const store = {
      deviceConfigSource: vi.fn().mockResolvedValue({
        deviceId: "d74f1594-765c-4bd5-b07b-6443273777ed",
        version: 2,
        state: {
          resourceBundleVersion: "1.0.0",
          wakeProfile: {
            id: "wake-bringup",
            version: 1,
            activation: {
              detectorId: "wakenet:hi_esp",
              sensitivity: 0.5,
              cooldownMs: 1_000,
            },
          },
        },
      }),
    } as unknown as ControlPlaneStore;

    await expect(new DeviceConfigService(store).snapshot(
      "d74f1594-765c-4bd5-b07b-6443273777ed",
    )).rejects.toThrow(/detectorId/);
  });

  it("does not sign one WakeNet model for both activation and interrupt", async () => {
    const store = {
      deviceConfigSource: vi.fn().mockResolvedValue({
        deviceId: "d74f1594-765c-4bd5-b07b-6443273777ed",
        version: 2,
        state: {
          resourceBundleVersion: "1.0.0",
          wakeProfile: {
            id: "wake-conflicting-roles",
            version: 1,
            activation: {
              detectorId: "wn9s_hiesp",
              sensitivity: 0.64,
              cooldownMs: 1_200,
            },
            interrupt: {
              detectorId: "wn9s_hiesp",
              sensitivity: 0.71,
              cooldownMs: 800,
              allowedStates: ["thinking", "speaking"],
            },
          },
        },
      }),
    } as unknown as ControlPlaneStore;

    await expect(new DeviceConfigService(store).snapshot(
      "d74f1594-765c-4bd5-b07b-6443273777ed",
    )).rejects.toThrow(/interrupt\.detectorId/);
  });
});

describe("matchesDeviceConfigEtag", () => {
  it("accepts quoted, weak, list and wildcard validators", () => {
    expect(matchesDeviceConfigEtag('"cfg1-current"', "cfg1-current")).toBe(true);
    expect(matchesDeviceConfigEtag('"cfg1-old", W/"cfg1-current"', "cfg1-current")).toBe(true);
    expect(matchesDeviceConfigEtag("*", "cfg1-current")).toBe(true);
    expect(matchesDeviceConfigEtag('"cfg1-old"', "cfg1-current")).toBe(false);
  });
});
