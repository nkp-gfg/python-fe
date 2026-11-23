/**
 * Server-render the passenger tree the flight board and comparison page mount.
 * The tree JSON is the payload from backend.api.routes.flights._build_tree_payload.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PassengerTree } from "@/components/dashboard/passenger-tree";
import type { FlightTree } from "@/lib/types";

const tree = JSON.parse(readFileSync(process.argv[2], "utf8")) as FlightTree;
const outPath = process.argv[3];

const surfaces = [
  { surface: "flight-board", mode: "tree" as const },
  { surface: "compare", mode: "combined" as const },
];

const rendered = surfaces.map(({ surface, mode }) => ({
  surface,
  mode,
  html: renderToStaticMarkup(createElement(PassengerTree, { tree, mode })),
}));

writeFileSync(outPath, JSON.stringify(rendered));
