import { createHash, createPublicKey, verify } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import canonicalize from "canonicalize";
import { describe, expect, it } from "vitest";

import { ContractRegistry, fixtureSchemaIds } from "./registry.js";

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const fixtureRoot = join(packageRoot, "fixtures");

function listJsonFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? listJsonFiles(path) : entry.name.endsWith(".json") ? [path] : [];
  });
}

describe("contract fixtures", () => {
  const registry = new ContractRegistry();

  it("maps every fixture to an explicit versioned schema", () => {
    const files = listJsonFiles(fixtureRoot)
      .map((path) => relative(fixtureRoot, path))
      .sort();
    expect(files).toEqual(Object.keys(fixtureSchemaIds).sort());
  });

  for (const [fixturePath, schemaId] of Object.entries(fixtureSchemaIds)) {
    it(`validates ${fixturePath}`, () => {
      const document = JSON.parse(readFileSync(join(fixtureRoot, fixturePath), "utf8")) as unknown;
      const result = registry.validate(schemaId, document);
      expect(result.errors, JSON.stringify(result.errors, null, 2)).toEqual([]);
      expect(result.valid).toBe(true);
    });
  }

  it("rejects a non-auto wake flow without silently coercing it", () => {
    const result = registry.validate("https://schemas.veetee.local/ws/control-event-v1.json", {
      session_id: "session-1",
      type: "listen",
      state: "start",
      mode: "push-to-submit",
      source: "wake_word",
    });
    expect(result.valid).toBe(false);
  });

  it("accepts bounded stop reasons and forward-compatible abort sources", () => {
    const listenStop = registry.validate("https://schemas.veetee.local/ws/control-event-v1.json", {
      session_id: "session-1",
      type: "listen",
      state: "stop",
      reason: "user_disable",
    });
    const wakeAbort = registry.validate("https://schemas.veetee.local/ws/control-event-v1.json", {
      session_id: "session-1",
      type: "abort",
      reason: "session_closing_cancelled",
      source: "wake_word",
    });
    expect(listenStop.valid).toBe(true);
    expect(wakeAbort.valid).toBe(true);
  });

  it("verifies the RFC 8785 JCS and Ed25519 release vector", () => {
    const vector = JSON.parse(
      readFileSync(join(fixtureRoot, "artifacts/signed-resource-manifest-vector-v1.json"), "utf8"),
    ) as {
      document: unknown;
      canonical_payload: string;
      public_key_spki_base64: string;
      signature_base64: string;
    };
    const canonicalPayload = canonicalize(vector.document);
    expect(canonicalPayload).toBe(vector.canonical_payload);

    const publicKey = createPublicKey({
      key: Buffer.from(vector.public_key_spki_base64, "base64"),
      format: "der",
      type: "spki",
    });
    expect(
      verify(
        null,
        Buffer.from(vector.canonical_payload, "utf8"),
        publicKey,
        Buffer.from(vector.signature_base64, "base64"),
      ),
    ).toBe(true);
  });

  it("verifies the complete development resource manifest", () => {
    const publicKey = createPublicKey({
      key: Buffer.from(
        "MCowBQYDK2VwAyEAI46wrFAWaXIburEHNLzXcKQWWrHWxJz7MNHie5CI17c=",
        "base64",
      ),
      format: "der",
      type: "spki",
    });
    for (const name of ["resource-manifest-v1.json", "resource-config-link-v1.json"]) {
      const manifest = JSON.parse(
        readFileSync(join(fixtureRoot, `artifacts/${name}`), "utf8"),
      ) as {
        signature: {
          algorithm: string;
          key_id: string;
          security_epoch: number;
          value: string;
        };
      } & Record<string, unknown>;
      const signature = manifest.signature.value;
      delete (manifest.signature as Partial<typeof manifest.signature>).value;
      const canonicalPayload = canonicalize(manifest);
      expect(canonicalPayload).toBeTypeOf("string");
      expect(
        verify(
          null,
          Buffer.from(canonicalPayload ?? "", "utf8"),
          publicKey,
          Buffer.from(signature, "base64"),
        ),
      ).toBe(true);
    }
  });

  it("requires a signed detector inventory in every ESP-SR V1 resource member", () => {
    const manifest = JSON.parse(
      readFileSync(join(fixtureRoot, "artifacts/resource-manifest-v1.json"), "utf8"),
    ) as { members: Array<Record<string, unknown>> };
    delete manifest.members[0]!.detectors;
    const result = registry.validate(
      "https://schemas.veetee.local/artifacts/resource-manifest-v1.json",
      manifest,
    );
    expect(result.valid).toBe(false);
  });

  it("verifies default-off and explicit wake-audio device config signatures", () => {
    const publicKey = createPublicKey({
      key: Buffer.from(
        "MCowBQYDK2VwAyEAI46wrFAWaXIburEHNLzXcKQWWrHWxJz7MNHie5CI17c=",
        "base64",
      ),
      format: "der",
      type: "spki",
    });
    for (const name of ["device-config-v1.json", "device-config-wake-audio-v1.json"]) {
      const config = JSON.parse(
        readFileSync(join(fixtureRoot, `config/${name}`), "utf8"),
      ) as {
        signature: {
          algorithm: string;
          key_id: string;
          security_epoch: number;
          value: string;
        };
      } & Record<string, unknown>;
      const signature = config.signature.value;
      delete (config.signature as Partial<typeof config.signature>).value;
      const canonicalPayload = canonicalize(config);
      expect(canonicalPayload).toBeTypeOf("string");
      expect(
        verify(
          null,
          Buffer.from(canonicalPayload ?? "", "utf8"),
          publicKey,
          Buffer.from(signature, "base64"),
        ),
      ).toBe(true);
    }
  });

  it("keeps bootstrap config version and ETag tied to the signed device body", () => {
    const config = JSON.parse(
      readFileSync(join(fixtureRoot, "config/device-config-v1.json"), "utf8"),
    ) as { version: number };
    const bootstrap = JSON.parse(
      readFileSync(join(fixtureRoot, "ota/bootstrap-bound.json"), "utf8"),
    ) as { config: { version: number; etag: string } };
    const canonicalBody = canonicalize(config);

    expect(bootstrap.config.version).toBe(config.version);
    expect(bootstrap.config.etag).toBe(
      `cfg1-${createHash("sha256").update(canonicalBody ?? "").digest("base64url")}`,
    );
  });

  it("accepts firmware OTA rebooting and pending-health report phases", () => {
    const base = JSON.parse(
      readFileSync(join(fixtureRoot, "devices/reported-state-v1.json"), "utf8"),
    ) as {
      state: {
        resource?: Record<string, unknown>;
        firmware_ota?: Record<string, unknown>;
      };
    };
    const firmwareOta = { ...base.state.resource! };
    delete base.state.resource;
    base.state.firmware_ota = firmwareOta;

    for (const phase of ["rebooting", "pending_health"]) {
      base.state.firmware_ota.phase = phase;
      const result = registry.validate(
        "https://schemas.veetee.local/devices/reported-state-v1.json",
        base,
      );
      expect(result.errors, JSON.stringify(result.errors, null, 2)).toEqual([]);
      expect(result.valid).toBe(true);
    }
  });
});
