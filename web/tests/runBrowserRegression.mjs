import { execFile, fork } from "node:child_process";
import { once } from "node:events";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const webRoot = fileURLToPath(new URL("../", import.meta.url));
const artifacts = resolve(webRoot, "../output/playwright/browser-regression");
const cliPath = fileURLToPath(import.meta.resolve("@playwright/cli/playwright-cli.js"));
const configPath = fileURLToPath(new URL("./playwright-ci.json", import.meta.url));
const regressionPath = fileURLToPath(new URL("./memoryHandoff.browser.js", import.meta.url));
const session = `secondpass-regression-${process.pid}`;
const controller = new AbortController();

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    process.exitCode = signal === "SIGINT" ? 130 : 143;
    controller.abort();
  });
}

async function runCli(args, timeout = 60_000, signal = controller.signal) {
  try {
    const { stdout, stderr } = await execute(process.execPath, [cliPath, `-s=${session}`, ...args], {
      cwd: artifacts,
      env: { ...process.env, NO_UPDATE_NOTIFIER: "1" },
      timeout,
      signal: signal ?? undefined,
      windowsHide: true,
    });
    process.stdout.write(stdout);
    process.stderr.write(stderr);
  } catch (error) {
    process.stdout.write(error.stdout ?? "");
    process.stderr.write(error.stderr ?? "");
    throw error;
  }
}

let previewProcess;
let previewExited;
let browserAttempted = false;
try {
  await mkdir(artifacts, { recursive: true });
  // Vite installs an exit-on-SIGTERM handler. Keep it out of the runner so
  // interrupted runs can await browser cleanup before stopping the preview.
  previewProcess = fork(new URL("./previewServer.mjs", import.meta.url), [], {
    cwd: webRoot,
    env: { ...process.env, CI: "true" },
    stdio: ["ignore", "inherit", "inherit", "ipc"],
    windowsHide: true,
  });
  previewExited = once(previewProcess, "exit");
  const previewFailure = previewExited.then(([code, signal]) => {
    throw new Error(`Preview exited unexpectedly (${code ?? signal})`);
  });
  const [message] = await Promise.race([
    once(previewProcess, "message", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)]),
    }),
    previewFailure,
  ]);
  if (message !== "ready") throw new Error("Unexpected preview startup response");
  await Promise.race([
    (async () => {
      browserAttempted = true;
      await runCli(["open", "about:blank", "--config", configPath, "--idle-timeout", "60000"]);
      await runCli(["run-code", "--filename", regressionPath]);
    })(),
    previewFailure,
  ]);
} catch (error) {
  console.error(error.message);
  process.exitCode ||= 1;
  controller.abort();
} finally {
  try {
    if (browserAttempted) await runCli(["close"], 15_000, null);
  } catch (error) {
    console.error(`Browser cleanup failed: ${error.message}`);
    process.exitCode ||= 1;
  } finally {
    if (previewProcess) {
      const forceStop = setTimeout(() => previewProcess.kill("SIGKILL"), 15_000);
      try {
        previewProcess.kill("SIGTERM");
        await previewExited;
      } finally {
        clearTimeout(forceStop);
      }
    }
  }
}
