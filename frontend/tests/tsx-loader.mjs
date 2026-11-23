import { createRequire } from "node:module";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve as resolvePath, extname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const frontendRoot = resolvePath(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(pathToFileURL(resolvePath(frontendRoot, "package.json")));

const EXTENSIONS = [".tsx", ".ts", ".jsx", ".js", ".mjs"];

function resolveAlias(specifier) {
  const base = resolvePath(frontendRoot, specifier.slice(2));
  return withExtension(base);
}

function withExtension(base) {
  if (existsSync(base) && statSync(base).isFile()) return base;
  for (const ext of EXTENSIONS) {
    if (existsSync(base + ext)) return base + ext;
  }
  for (const ext of EXTENSIONS) {
    const index = resolvePath(base, "index" + ext);
    if (existsSync(index)) return index;
  }
  return base;
}

function isProjectFile(parentURL) {
  if (!parentURL || !parentURL.startsWith("file:")) return false;
  const path = fileURLToPath(parentURL);
  return path.startsWith(frontendRoot) && !path.includes("/node_modules/");
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    return { url: pathToFileURL(resolveAlias(specifier)).href, shortCircuit: true };
  }
  if ((specifier.startsWith("./") || specifier.startsWith("../")) && isProjectFile(context.parentURL)) {
    const parent = fileURLToPath(context.parentURL);
    return {
      url: pathToFileURL(withExtension(resolvePath(dirname(parent), specifier))).href,
      shortCircuit: true,
    };
  }
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (error && error.code === "ERR_MODULE_NOT_FOUND" && !specifier.startsWith(".") && !specifier.startsWith("file:")) {
      const resolved = require.resolve(specifier);
      return { url: pathToFileURL(resolved).href, shortCircuit: true };
    }
    throw error;
  }
}

export async function load(url, context, nextLoad) {
  if (url.startsWith("file:")) {
    const path = fileURLToPath(url);
    const ext = extname(path);
    if ((ext === ".ts" || ext === ".tsx") && path.startsWith(frontendRoot) && !path.includes("/node_modules/")) {
      const source = readFileSync(path, "utf8");
      const transpiled = ts.transpileModule(source, {
        compilerOptions: {
          module: ts.ModuleKind.ESNext,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
          esModuleInterop: true,
          isolatedModules: true,
        },
        fileName: path,
      });
      return { format: "module", source: transpiled.outputText, shortCircuit: true };
    }
  }
  return nextLoad(url, context);
}
