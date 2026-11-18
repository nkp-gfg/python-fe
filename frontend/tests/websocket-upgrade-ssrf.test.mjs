/**
 * Spec for the self-hosted Next.js WebSocket upgrade SSRF
 * (GHSA-c4j6-fc7j-m34r / CVE-2026-44578).
 *
 * A network client can send an absolute-form WebSocket upgrade to the
 * built-in Node server (`next start` and `next dev`). While next is 16.2.0
 * the server opens that URL itself. These assertions fail until next is
 * 16.2.5 or later, which refuses the proxy unless routing selected a safe
 * external rewrite.
 *
 * `next build` on this app fails static generation for unrelated page
 * errors, so the production server is built with Next's compile-only mode.
 * That still serves this app through `next start` and the same upgrade handler.
 *
 * Run from frontend/:
 *   npm ci
 *   node --test --test-concurrency=1 tests/websocket-upgrade-ssrf.test.mjs
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nextBin = path.join(frontendDir, "node_modules", "next", "dist", "bin", "next");
const probePath = "/secret-upgrade";
const internalMarker = "SSRF_CONFIRMED_INTERNAL_ONLY";

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function startInternalService() {
  const hits = [];
  const server = http.createServer((req, res) => {
    hits.push({ event: "request", method: req.method, url: req.url });
    res.writeHead(200, { "content-type": "text/plain", connection: "close" });
    res.end(internalMarker);
  });
  server.on("upgrade", (req, socket) => {
    hits.push({ event: "upgrade", method: req.method, url: req.url });
    const body = internalMarker;
    socket.write(
      `HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: text/plain\r\nContent-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`
    );
    socket.end();
  });
  return { server, hits };
}

function sendRaw(port, payload) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    const chunks = [];
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve(Buffer.concat(chunks).toString("latin1"));
    };
    socket.once("connect", () => socket.write(payload));
    socket.on("data", (chunk) => chunks.push(chunk));
    socket.once("error", (err) => {
      if (settled) return;
      settled = true;
      reject(err);
    });
    socket.setTimeout(2000, () => socket.destroy());
    socket.once("close", finish);
  });
}

function upgradePayload(nextPort, requestTarget) {
  return Buffer.from(
    `${requestTarget} HTTP/1.1\r\n` +
      `Host: 127.0.0.1:${nextPort}\r\n` +
      "Connection: Upgrade\r\n" +
      "Upgrade: websocket\r\n" +
      "Sec-WebSocket-Version: 13\r\n" +
      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
      "\r\n",
    "latin1"
  );
}

async function getFreePort() {
  const server = net.createServer();
  const port = await listen(server);
  await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
  return port;
}

function spawnNext(args) {
  const child = spawn(process.execPath, [nextBin, ...args], {
    cwd: frontendDir,
    detached: true,
    env: {
      ...process.env,
      NEXT_TELEMETRY_DISABLED: "1",
      NODE_ENV: args[0] === "start" ? "production" : "development",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.stderr.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.output = () => output;
  return child;
}

async function stopNext(child) {
  if (!child || child.exitCode !== null) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
  const exited = once(child, "exit");
  const timedOut = new Promise((resolve) => setTimeout(resolve, 5000, "timeout"));
  if ((await Promise.race([exited.then(() => "exit"), timedOut])) === "timeout") {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      child.kill("SIGKILL");
    }
  }
}

async function waitForHttp(port, child) {
  const deadline = Date.now() + 90_000;
  let lastError = "";
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Next exited ${child.exitCode} before serving HTTP\n${child.output()}`);
    }
    try {
      const status = await new Promise((resolve, reject) => {
        const req = http.get(`http://127.0.0.1:${port}/`, (res) => {
          res.resume();
          resolve(res.statusCode);
        });
        req.setTimeout(5000, () => req.destroy(new Error("timeout")));
        req.on("error", reject);
      });
      if (status && status < 500) return;
      lastError = `status ${status}`;
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Next server on ${port} did not serve HTTP (${lastError})\n${child.output()}`);
}

async function runNext(mode) {
  const port = await getFreePort();
  const args =
    mode === "start"
      ? ["start", "-H", "127.0.0.1", "-p", String(port)]
      : ["dev", "-H", "127.0.0.1", "-p", String(port)];
  const child = spawnNext(args);
  try {
    await waitForHttp(port, child);
  } catch (err) {
    await stopNext(child);
    throw err;
  }
  return { port, child };
}

async function buildProduction() {
  const child = spawn(process.execPath, [nextBin, "build", "--experimental-build-mode", "compile"], {
    cwd: frontendDir,
    env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.stderr.on("data", (chunk) => {
    output += chunk.toString();
  });
  const [code] = await once(child, "exit");
  if (code !== 0) throw new Error(`next build exited ${code}\n${output}`);
}

async function withInternal(fn) {
  const internal = startInternalService();
  const port = await listen(internal.server);
  try {
    await fn({ ...internal, port });
  } finally {
    await new Promise((resolve, reject) =>
      internal.server.close((err) => (err ? reject(err) : resolve()))
    );
  }
}

function assertNotProxied(backend, response) {
  assert.equal(
    backend.hits.some((hit) => String(hit.url || "").includes(probePath)),
    false,
    `internal service received ${JSON.stringify(backend.hits)}`
  );
  assert.equal(response.includes(internalMarker), false);
}

describe("self-hosted Next WebSocket upgrade SSRF", { concurrency: 1, timeout: 180_000 }, () => {
  test("compile the app that next start serves", async () => {
    await buildProduction();
  });

  for (const mode of ["start", "dev"]) {
    test(`${mode}: absolute-form WebSocket upgrade is not proxied`, async () => {
      await withInternal(async (backend) => {
        const next = await runNext(mode);
        try {
          const response = await sendRaw(
            next.port,
            upgradePayload(next.port, `GET http://127.0.0.1:${backend.port}${probePath}`)
          );
          assertNotProxied(backend, response);
        } finally {
          await stopNext(next.child);
        }
      });
    });
  }

  test("start: ordinary absolute-form GET is not proxied", async () => {
    await withInternal(async (backend) => {
      const next = await runNext("start");
      try {
        const response = await sendRaw(
          next.port,
          Buffer.from(
            `GET http://127.0.0.1:${backend.port}${probePath} HTTP/1.1\r\n` +
              `Host: 127.0.0.1:${next.port}\r\n` +
              "Connection: close\r\n\r\n",
            "latin1"
          )
        );
        assert.equal(
          backend.hits.some((hit) => String(hit.url || "").includes(probePath)),
          false,
          `internal service received ${JSON.stringify(backend.hits)}`
        );
        assert.equal(response.includes("308"), true);
      } finally {
        await stopNext(next.child);
      }
    });
  });
});
