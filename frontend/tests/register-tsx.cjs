/**
 * Load the frontend's TypeScript sources from a Node test.
 * Installed packages resolve through Node. App files compile with the
 * TypeScript package already declared by the frontend.
 */
const fs = require("fs");
const path = require("path");
const Module = require("module");
const ts = require("typescript");

const FRONTEND_ROOT = path.resolve(__dirname, "..");

const compilerOptions = {
  module: ts.ModuleKind.CommonJS,
  target: ts.ScriptTarget.ES2022,
  jsx: ts.JsxEmit.ReactJSX,
  esModuleInterop: true,
  allowSyntheticDefaultImports: true,
  moduleResolution: ts.ModuleResolutionKind.Node10,
};

function compile(filename, source) {
  const transpiled = ts.transpileModule(source, {
    compilerOptions,
    fileName: filename,
    reportDiagnostics: true,
  });
  const errors = (transpiled.diagnostics || []).filter(
    (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
  );
  if (errors.length > 0) {
    const message = errors
      .map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"))
      .join("\n");
    throw new Error(`TypeScript compile failed for ${filename}:\n${message}`);
  }
  return transpiled.outputText;
}

function install(extension) {
  Module._extensions[extension] = function loadTypeScript(module, filename) {
    const source = fs.readFileSync(filename, "utf8");
    module._compile(compile(filename, source), filename);
  };
}

install(".ts");
install(".tsx");

const originalResolve = Module._resolveFilename;

function localFile(request, parent) {
  let base = request;
  if (request.startsWith("@/")) {
    base = path.join(FRONTEND_ROOT, request.slice(2));
  } else if (request.startsWith(".")) {
    const parentFile = parent && parent.filename ? parent.filename : FRONTEND_ROOT;
    base = path.resolve(path.dirname(parentFile), request);
  } else {
    return null;
  }

  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    path.join(base, "index.tsx"),
    path.join(base, "index.ts"),
    path.join(base, "index.js"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  return null;
}

Module._resolveFilename = function resolveFilename(request, parent, isMain, options) {
  if (request.endsWith(".css")) {
    return path.join(__dirname, "empty-module.cjs");
  }
  const local = localFile(request, parent);
  if (local) return local;
  return originalResolve.call(this, request, parent, isMain, options);
};
