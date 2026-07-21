import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";

function valueFromLines(document, name) {
  const prefix = `${name}: `;
  const line = document.split(/\r?\n/).find((candidate) => candidate.startsWith(prefix));
  if (!line) throw new Error(`Missing ${name} in local credentials`);
  return line.slice(prefix.length);
}

async function jsonRequest(url, init) {
  const response = await fetch(url, init);
  const body = await response.json();
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${url} returned ${response.status}`);
  return body;
}

async function main() {
  const credentials = await readFile(new URL("../data/local-admin.txt", import.meta.url), "utf8");
  const managerUrl = valueFromLines(credentials, "Manager URL");
  const email = valueFromLines(credentials, "Email");
  const password = valueFromLines(credentials, "Password");
  const hardwareId = `e2e-${randomBytes(8).toString("hex")}`;
  const deviceHeaders = {
    "Content-Type": "application/json",
    "Device-Id": hardwareId,
    "Client-Id": `client-${randomBytes(8).toString("hex")}`,
    "Device-Model": "veetee-s3-n16r8",
    "Firmware-Version": "0.2.0",
    "Accept-Language": "vi-VN",
  };

  const bootstrap = await jsonRequest(`${managerUrl}/veetee/ota`, {
    method: "POST",
    headers: deviceHeaders,
    body: "{}",
  });
  const login = await jsonRequest(`${managerUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, tenantSlug: "veetee-local" }),
  });
  await jsonRequest(
    `${managerUrl}/api/v1/devices/activation/${encodeURIComponent(bootstrap.activation.code)}/bind`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${login.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: "Artifact E2E Device" }),
    },
  );
  const activation = await jsonRequest(`${managerUrl}/veetee/ota/activate`, {
    method: "POST",
    headers: deviceHeaders,
    body: JSON.stringify({ hardwareId, challenge: bootstrap.activation.challenge }),
  });
  if (activation.status !== "active" || typeof activation.token !== "string") {
    throw new Error("Device activation did not return a token");
  }

  const authorization = {
    Authorization: `Bearer ${activation.token}`,
    "Device-Id": hardwareId,
  };
  const activeBootstrap = await jsonRequest(`${managerUrl}/veetee/ota`, {
    method: "POST",
    headers: { ...deviceHeaders, ...authorization },
    body: "{}",
  });
  const reportBase = {
    bootId: "95eff5a6-3dcf-4cb4-a6d9-e31cd6d82f63",
    state: {
      schemaVersion: 1,
      firmware: { version: "0.2.0" },
      resource: {
        phase: "checking",
        currentVersion: "factory-bringup",
        desiredVersion: activeBootstrap.resources.version,
        activeSlot: 0,
        targetSlot: 1,
        expectedBytes: 0,
        downloadedBytes: 0,
        securityEpoch: 1,
      },
    },
  };
  const reported = await jsonRequest(
    `${managerUrl}/veetee/devices/${encodeURIComponent(activation.device_id)}/reported-state`,
    {
      method: "PUT",
      headers: { ...authorization, "Content-Type": "application/json" },
      body: JSON.stringify({ ...reportBase, version: 1 }),
    },
  );
  const equalRetry = await jsonRequest(
    `${managerUrl}/veetee/devices/${encodeURIComponent(activation.device_id)}/reported-state`,
    {
      method: "PUT",
      headers: { ...authorization, "Content-Type": "application/json" },
      body: JSON.stringify({
        ...reportBase,
        version: 1,
        state: {
          ...reportBase.state,
          resource: { ...reportBase.state.resource, phase: "verifying" },
        },
      }),
    },
  );
  if (
    reported.reportedState.state.resource.phase !== "checking" ||
    equalRetry.reportedState.state.resource.phase !== "checking"
  ) {
    throw new Error("Equal reported-state retry mutated stored state");
  }
  const activeReport = await jsonRequest(
    `${managerUrl}/veetee/devices/${encodeURIComponent(activation.device_id)}/reported-state`,
    {
      method: "PUT",
      headers: { ...authorization, "Content-Type": "application/json" },
      body: JSON.stringify({
        ...reportBase,
        version: 2,
        state: {
          ...reportBase.state,
          resource: {
            ...reportBase.state.resource,
            phase: "active",
            currentVersion: activeBootstrap.resources.version,
            activeSlot: 1,
            targetSlot: 1,
          },
        },
      }),
    },
  );
  if (activeReport.reportedState.version !== 2) {
    throw new Error("Reported-state sequence did not advance");
  }
  const staleReport = await fetch(
    `${managerUrl}/veetee/devices/${encodeURIComponent(activation.device_id)}/reported-state`,
    {
      method: "PUT",
      headers: { ...authorization, "Content-Type": "application/json" },
      body: JSON.stringify({ ...reportBase, version: 1 }),
    },
  );
  if (staleReport.status !== 409) {
    throw new Error(`Stale reported state returned ${staleReport.status}`);
  }
  const manifestResponse = await fetch(activeBootstrap.resources.manifest_url, {
    headers: authorization,
  });
  if (!manifestResponse.ok) throw new Error(`Manifest returned ${manifestResponse.status}`);
  const manifest = await manifestResponse.json();

  const contentResponse = await fetch(manifest.payload.url, { headers: authorization });
  if (!contentResponse.ok) throw new Error(`Content returned ${contentResponse.status}`);
  const content = Buffer.from(await contentResponse.arrayBuffer());
  const hash = createHash("sha256").update(content).digest("hex");
  if (content.length !== manifest.payload.size || hash !== manifest.payload.sha256) {
    throw new Error("Full artifact response does not match signed manifest metadata");
  }

  const resumeOffset = Math.min(64 * 1024, content.length - 1);
  const rangeResponse = await fetch(manifest.payload.url, {
    headers: { ...authorization, Range: `bytes=${resumeOffset}-` },
  });
  if (
    rangeResponse.status !== 206 ||
    rangeResponse.headers.get("content-range") !==
      `bytes ${resumeOffset}-${content.length - 1}/${content.length}`
  ) {
    throw new Error("Artifact Range response contract failed");
  }
  const tail = Buffer.from(await rangeResponse.arrayBuffer());
  if (!tail.equals(content.subarray(resumeOffset))) {
    throw new Error("Artifact Range body differs from full content");
  }

  const invalidRange = await fetch(manifest.payload.url, {
    headers: { ...authorization, Range: `bytes=${content.length}-` },
  });
  if (
    invalidRange.status !== 416 ||
    invalidRange.headers.get("content-range") !== `bytes */${content.length}`
  ) {
    throw new Error("Artifact invalid Range response contract failed");
  }
  process.stdout.write(
    `${JSON.stringify({
      status: "ok",
      manifestId: "stable",
      bytes: content.length,
      fullStatus: contentResponse.status,
      rangeStatus: rangeResponse.status,
      invalidRangeStatus: invalidRange.status,
      reportedStateVersion: activeReport.reportedState.version,
      staleReportStatus: staleReport.status,
    })}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
