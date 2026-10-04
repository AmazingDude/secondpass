import { fileURLToPath } from "node:url";
import { preview } from "vite";

// Private worker: Vite's process-wide signal handling belongs in this process.
await preview({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  preview: { host: "127.0.0.1", port: 5174, strictPort: true },
});
process.send("ready");
