import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const cliPath = fileURLToPath(import.meta.resolve("@playwright/cli/playwright-cli.js"));
const artifacts = fileURLToPath(new URL("../../output/playwright/browser-regression/", import.meta.url));
const execute = promisify(execFile);

test("SIGTERM fails the browser gate and closes its owned browser and preview", {
  skip: process.platform === "win32" ? "Windows does not deliver POSIX SIGTERM" : false,
  timeout: 120_000,
}, async (t) => {
  const runner = spawn(process.execPath, ["tests/runBrowserRegression.mjs"], {
    cwd: webRoot,
    env: { ...process.env, CI: "true" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const closed = once(runner, "close");
  let output = "";
  let session;
  t.after(async () => {
    const forceStop = setTimeout(() => runner.kill("SIGKILL"), 15_000);
    try {
      runner.kill("SIGTERM");
      await closed;
      if (session) {
        await execute(process.execPath, [cliPath, `-s=${session}`, "close"], { cwd: artifacts, timeout: 15_000 });
      }
    } finally {
      clearTimeout(forceStop);
    }
  });
  const opened = new Promise((resolve, reject) => {
    runner.stdout.on("data", (chunk) => {
      output += chunk;
      const match = /### Browser `([^`]+)` opened/.exec(output);
      if (match) resolve(match[1]);
    });
    runner.stderr.on("data", (chunk) => { output += chunk; });
    closed.then(() => reject(new Error(`Runner exited before browser startup: ${output}`)), reject);
  });
  session = await opened;
  runner.kill("SIGTERM");
  const [code] = await closed;
  assert.equal(code, 143, output);
  assert.ok(output.includes(`Browser '${session}' closed`), output);
  const { stdout } = await execute(process.execPath, [cliPath, "list"], { cwd: artifacts, timeout: 15_000 });
  assert.ok(!stdout.includes(session), stdout);

  const portProbe = createServer();
  try {
    const listening = once(portProbe, "listening");
    portProbe.listen(5174, "127.0.0.1");
    await listening;
  } finally {
    if (portProbe.listening) await new Promise((resolve) => portProbe.close(resolve));
  }
});
