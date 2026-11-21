// Runs the flight-board re-ingest function from source.
// stdin: one FlightListItem JSON object, as GET /flights returns it.
// stdout: {"mutateCalls": number, "departureDateTime": string | null}

import fs from "node:fs";

const workbenchPath = process.argv[2];
const source = fs.readFileSync(workbenchPath, "utf8");
const flight = JSON.parse(fs.readFileSync(0, "utf8"));

function scanToMatchingBrace(src, openIndex) {
  // stack frames: code (template ${...} expressions) | str | tmpl
  const stack = [];
  let depth = 0;
  let i = openIndex;
  while (i < src.length) {
    const ch = src[i];
    const next = src[i + 1];
    const frame = stack[stack.length - 1];

    if (frame && frame.type === "str") {
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === frame.q) stack.pop();
      i += 1;
      continue;
    }
    if (frame && frame.type === "tmpl") {
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === "`") {
        stack.pop();
        i += 1;
        continue;
      }
      if (ch === "$" && next === "{") {
        stack.push({ type: "code", depth });
        depth += 1;
        i += 2;
        continue;
      }
      i += 1;
      continue;
    }

    if (ch === "/" && next === "/") {
      const nl = src.indexOf("\n", i);
      i = nl < 0 ? src.length : nl;
      continue;
    }
    if (ch === "/" && next === "*") {
      const end = src.indexOf("*/", i + 2);
      i = end < 0 ? src.length : end + 2;
      continue;
    }
    if (ch === "'" || ch === '"') {
      stack.push({ type: "str", q: ch });
      i += 1;
      continue;
    }
    if (ch === "`") {
      stack.push({ type: "tmpl" });
      i += 1;
      continue;
    }
    if (ch === "/" && isRegexStart(src, i)) {
      i = skipRegex(src, i);
      continue;
    }
    if (ch === "{") {
      depth += 1;
      i += 1;
      continue;
    }
    if (ch === "}") {
      depth -= 1;
      if (frame && frame.type === "code" && depth === frame.depth) stack.pop();
      if (depth === 0) return i;
      i += 1;
      continue;
    }
    i += 1;
  }
  throw new Error("unterminated function");
}

function isRegexStart(src, index) {
  let j = index - 1;
  while (j >= 0 && /\s/.test(src[j])) j -= 1;
  if (j < 0) return true;
  return "([{:;,=!?&|~+-*%^<>".includes(src[j]) || src.slice(j - 5, j + 1) === "return";
}

function skipRegex(src, index) {
  let i = index + 1;
  let inClass = false;
  while (i < src.length) {
    const ch = src[i];
    if (ch === "\\") {
      i += 2;
      continue;
    }
    if (ch === "[") inClass = true;
    else if (ch === "]") inClass = false;
    else if (ch === "/" && !inClass) {
      i += 1;
      while (i < src.length && /[a-z]/i.test(src[i])) i += 1;
      return i;
    } else if (ch === "\n") {
      return i;
    }
    i += 1;
  }
  return i;
}

function extractFunction(src, name) {
  const needle = `function ${name}`;
  const start = src.indexOf(needle);
  if (start < 0) throw new Error(`missing function ${name}`);
  const open = src.indexOf("{", start);
  const close = scanToMatchingBrace(src, open);
  return src.slice(start, close + 1);
}

function stripSignature(fnSource) {
  const open = fnSource.indexOf("{");
  const head = fnSource.slice(0, open);
  const body = fnSource.slice(open);
  const name = head.match(/^function\s+([A-Za-z0-9_]+)\s*\(/)[1];
  const paramsStart = head.indexOf("(");
  const paramsEnd = head.lastIndexOf(")");
  const params = head.slice(paramsStart + 1, paramsEnd);
  const names = params
    .split(",")
    .map((param) => param.trim())
    .filter(Boolean)
    .map((param) => param.split(":")[0].trim().replace(/^\.\.\./, "").replace(/\?$/, ""));
  return `function ${name}(${names.join(", ")}) ${body}`;
}

const names = ["toIsoDateTime", "executeIngest"];
let result = null;
for (let attempt = 0; attempt < 12; attempt += 1) {
  const prelude = names
    .map((name) => stripSignature(extractFunction(source, name)))
    .join("\n\n");
  const runner = new Function(
    "flight",
    `let confirmFlight = flight;
     let mutateCalls = 0;
     let sent = null;
     function setConfirmFlight(value) { confirmFlight = value; }
     function setIngestingFlight() {}
     const ingestMutation = {
       mutate(payload) {
         mutateCalls += 1;
         sent = payload;
       },
     };
     ${prelude}
     executeIngest();
     return { mutateCalls, departureDateTime: sent ? sent.departureDateTime : null, payload: sent };`,
  );
  try {
    result = runner(flight);
    break;
  } catch (error) {
    const missing = String(error && error.message).match(/^([A-Za-z_$][\w$]*) is not defined$/);
    if (!missing || names.includes(missing[1])) throw error;
    names.push(missing[1]);
  }
}
if (!result) throw new Error("could not run executeIngest");
process.stdout.write(JSON.stringify(result));
