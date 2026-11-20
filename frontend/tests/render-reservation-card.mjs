/**
 * Render the real ReservationCard for one list-endpoint reservation.
 * Reads the reservation JSON from stdin and writes static HTML to stdout.
 *
 * esbuild is not a runtime dependency of the app. Install it before running:
 *   npm install --no-save esbuild
 */
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(frontendRoot, "package.json"));

let esbuild;
try {
  esbuild = require("esbuild");
} catch {
  process.stderr.write(
    "esbuild is required to render reservation-view.tsx. From frontend/, run: npm install --no-save esbuild\n",
  );
  process.exit(2);
}

const reservationJson = readFileSync(0, "utf8");

const exportCard = {
  name: "export-reservation-card",
  setup(build) {
    build.onLoad({ filter: /reservation-view\.tsx$/ }, async (args) => {
      let src = readFileSync(args.path, "utf8");
      if (!src.includes("export function ReservationCard")) {
        src = src.replace(
          "function ReservationCard(",
          "export function ReservationCard(",
        );
      }
      return { contents: src, loader: "tsx" };
    });
  },
};

const outfile = join(mkdtempSync(join(tmpdir(), "reservation-card-")), "render.cjs");

await esbuild.build({
  absWorkingDir: frontendRoot,
  stdin: {
    contents: `
      import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import { ReservationCard } from "./components/dashboard/reservation-view.tsx";

      export function renderReservation(reservation) {
        return renderToStaticMarkup(
          React.createElement(ReservationCard, {
            reservation,
            expanded: true,
            onToggle() {},
          }),
        );
      }
    `,
    resolveDir: frontendRoot,
    loader: "tsx",
    sourcefile: "render-reservation-card.tsx",
  },
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile,
  jsx: "automatic",
  alias: { "@": frontendRoot },
  plugins: [exportCard],
  logLevel: "silent",
});

const { renderReservation } = require(outfile);
const html = renderReservation(JSON.parse(reservationJson));
writeFileSync(1, html);
