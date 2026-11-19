import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { transformSync } from "next/dist/build/swc/index.js";

const root = fileURLToPath(new URL("..", import.meta.url));

function existing(file) {
  const candidates = [
    file,
    `${file}.ts`,
    `${file}.tsx`,
    join(file, "index.ts"),
    join(file, "index.tsx"),
  ];
  for (const candidate of candidates) {
    try {
      readFileSync(candidate);
      return candidate;
    } catch {
      // keep looking
    }
  }
  return null;
}

function localFile(specifier, parentPath) {
  if (specifier.startsWith("@/")) {
    return existing(resolve(root, specifier.slice(2)));
  }
  if (specifier.startsWith(".")) {
    return existing(resolve(dirname(parentPath), specifier));
  }
  return null;
}

function rewriteSpecifiers(code, parentPath) {
  return code.replace(
    /(from\s+|import\s*\(\s*)(["'])([^"']+)\2/g,
    (full, prefix, quote, specifier) => {
      const file = localFile(specifier, parentPath);
      if (!file) return full;
      return `${prefix}${quote}${pathToFileURL(file).href}${quote}`;
    },
  );
}

export async function load(url, context, nextLoad) {
  if (!url.startsWith("file:")) return nextLoad(url, context);
  const path = fileURLToPath(url.split("?")[0]);
  if (!path.endsWith(".ts") && !path.endsWith(".tsx")) {
    return nextLoad(url, context);
  }
  let source = readFileSync(path, "utf8");
  if (path.endsWith(`${join("components", "dashboard", "passenger-detail-sheet.tsx")}`)) {
    source += "\nexport { ItinerarySection };\n";
  }
  const transformed = transformSync(source, {
    filename: path,
    jsc: {
      parser: {
        syntax: "typescript",
        tsx: path.endsWith(".tsx"),
        decorators: false,
      },
      transform: {
        react: { runtime: "automatic", importSource: "react" },
      },
      target: "es2022",
    },
    module: { type: "es6" },
    sourceMaps: false,
  });
  const code = rewriteSpecifiers(transformed.code, path);
  return { format: "module", source: code, shortCircuit: true };
}
