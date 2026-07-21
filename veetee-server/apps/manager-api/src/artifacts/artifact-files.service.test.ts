import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ForbiddenException } from "@nestjs/common";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ArtifactFilesService,
  ArtifactRangeNotSatisfiableException,
  parseByteRange,
} from "./artifact-files.service.js";

async function readStream(stream: NodeJS.ReadableStream): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

describe("ArtifactFilesService", () => {
  let root = "";
  const service = new ArtifactFilesService();

  beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), "veetee-artifacts-"));
    await mkdir(join(root, "stable"));
    await writeFile(join(root, "stable", "manifest.json"), '{"manifest_version":1}');
    await writeFile(join(root, "stable", "content.bin"), Buffer.from("0123456789"));
    await writeFile(join(root, "stable", ".complete"), "fixture  content.bin\n");
    vi.stubEnv("VEETEE_ARTIFACT_ROOT", root);
    vi.stubEnv("VEETEE_RESOURCE_MANIFEST_ID", "stable");
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(root, { recursive: true, force: true });
  });

  it("streams a bounded manifest without buffering it", async () => {
    const response = await service.openManifest("stable");
    expect(response.statusCode).toBe(200);
    expect(response.headers["Content-Length"]).toBe("22");
    expect((await readStream(response.stream)).toString()).toBe('{"manifest_version":1}');
  });

  it("serves an exact resumable byte range", async () => {
    const response = await service.openContent("stable", "bytes=4-");
    expect(response.statusCode).toBe(206);
    expect(response.headers["Content-Range"]).toBe("bytes 4-9/10");
    expect(response.headers["Content-Length"]).toBe("6");
    expect((await readStream(response.stream)).toString()).toBe("456789");
  });

  it("rejects multiple, suffix and out-of-bounds ranges", () => {
    expect(() => parseByteRange("bytes=0-1,3-4", 10)).toThrow(
      ArtifactRangeNotSatisfiableException,
    );
    expect(() => parseByteRange("bytes=-4", 10)).toThrow(
      ArtifactRangeNotSatisfiableException,
    );
    expect(() => parseByteRange("bytes=10-", 10)).toThrow(
      ArtifactRangeNotSatisfiableException,
    );
  });

  it("limits access to the manifest selected for the device", () => {
    expect(() => service.assertDeviceAccess("stable", {})).not.toThrow();
    expect(() =>
      service.assertDeviceAccess("canary", { resourceManifestId: "stable" }),
    ).toThrow(ForbiddenException);
  });

  it("does not expose a release until its completion marker exists", async () => {
    await mkdir(join(root, "incomplete"));
    await writeFile(join(root, "incomplete", "manifest.json"), "{}");
    await expect(service.openManifest("incomplete")).rejects.toThrow(
      "Artifact not found",
    );
  });

  it("does not follow an artifact directory symlink outside the root", async () => {
    const outside = await mkdtemp(join(tmpdir(), "veetee-artifacts-outside-"));
    try {
      await writeFile(join(outside, "manifest.json"), "{}");
      await writeFile(join(outside, ".complete"), "fixture\n");
      await symlink(outside, join(root, "escaped"), "dir");
      await expect(service.openManifest("escaped")).rejects.toThrow(
        "Artifact not found",
      );
    } finally {
      await rm(outside, { recursive: true, force: true });
    }
  });
});
