import assert from "node:assert/strict";
import test from "node:test";

import {
  buildReleaseMetadata,
  repositoryProvenance,
  sha256Buffer,
} from "./release-provenance.mjs";

test("builds bounded provenance and SPDX metadata without private paths", () => {
  const content = Buffer.from("firmware fixture", "utf8");
  const manifestText = '{"manifest_version":1}\n';
  const { provenance, sbom } = buildReleaseMetadata({
    artifactId: "fw-0.3.1",
    kind: "firmware",
    version: "0.3.1",
    channel: "canary",
    contentFileName: "content.bin",
    contentBytes: content.length,
    contentSha256: sha256Buffer(content),
    manifestText,
    generatedAt: "2026-07-24T00:00:00.000Z",
    sourceFileName: "/private/build/veetee_firmware.bin",
    repository: { commit: "abc123", dirty: false },
    nodeVersion: "v22.0.0",
  });

  assert.deepEqual(provenance.source, {
    file_name: "veetee_firmware.bin",
    content_file: "content.bin",
  });
  assert.equal(provenance.manifest.sha256, sha256Buffer(Buffer.from(manifestText)));
  assert.equal(provenance.build.repository.commit, "abc123");
  assert.equal(provenance.build.node_version, "v22.0.0");
  assert.equal(sbom.spdxVersion, "SPDX-2.3");
  assert.equal(sbom.packages[0].checksums[0].checksumValue, provenance.content.sha256);
  assert.equal(JSON.stringify(provenance).includes("/private/"), false);
});

test("repository provenance degrades when git metadata is unavailable", () => {
  assert.deepEqual(repositoryProvenance("/path/that/does/not/exist"), {
    commit: null,
    dirty: null,
  });
});
