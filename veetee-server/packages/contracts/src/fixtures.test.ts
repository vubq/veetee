import { createPublicKey, verify } from "node:crypto";
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
});
