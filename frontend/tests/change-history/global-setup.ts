import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const serverScript = path.join(repoRoot, "backend/tests/changes_tab_history_server.py");

let server: ChildProcess | undefined;

function pythonBinary(): string {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venv = path.join(repoRoot, "backend/tests/.venv/bin/python");
  if (existsSync(venv)) return venv;
  return "python3";
}

export async function setup(): Promise<void> {
  const python = pythonBinary();
  server = spawn(python, [serverScript], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: repoRoot },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let output = "";
  const ready = new Promise<string>((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`change history server did not start\n${output}`));
    }, 45_000);
    const onData = (chunk: Buffer) => {
      output += chunk.toString();
      const match = output.match(/READY (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(match[1]);
      }
    };
    server?.stdout?.on("data", onData);
    server?.stderr?.on("data", (chunk: Buffer) => {
      output += chunk.toString();
    });
    server?.on("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`change history server exited ${code}\n${output}`));
    });
  });

  const baseUrl = await ready;
  writeFileSync("/tmp/falconeye-change-history-url", baseUrl);
  console.log(`change history API ${baseUrl}`);
}

export async function teardown(): Promise<void> {
  server?.kill("SIGTERM");
}
