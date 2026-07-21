import { spawn } from "node:child_process";
import process from "node:process";

const tscCommand = process.platform === "win32" ? "tsc.cmd" : "tsc";
const children = new Set();
let shuttingDown = false;

function start(command, args) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env: process.env,
  });
  children.add(child);
  child.once("close", () => children.delete(child));
  return child;
}

function stop(signal = "SIGTERM") {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;
  for (const child of children) {
    child.kill(signal);
  }
}

function waitForClose(child) {
  return new Promise((resolve) => {
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    stop(signal);
    process.exitCode = signal === "SIGINT" ? 130 : 143;
  });
}

const initialBuild = start(tscCommand, ["-p", "tsconfig.json"]);
const buildResult = await waitForClose(initialBuild);
if (buildResult.code !== 0) {
  process.exitCode = buildResult.code ?? 1;
} else {
  const compiler = start(tscCommand, [
    "-p",
    "tsconfig.json",
    "--watch",
    "--preserveWatchOutput",
  ]);
  const server = start(process.execPath, ["--watch", "--env-file=.env", "dist/main.js"]);

  const result = await Promise.race([waitForClose(compiler), waitForClose(server)]);
  if (!shuttingDown) {
    stop();
    process.exitCode = result.code ?? (result.signal ? 1 : 0);
  }
}
