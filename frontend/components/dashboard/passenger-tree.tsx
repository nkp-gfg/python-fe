"use client";

import type { FlightTree } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Network } from "lucide-react";
import { PaxTree } from "@/components/dashboard/pax-tree";
import { PaxMatrix } from "@/components/dashboard/pax-matrix";

/**
 * Unified wrapper that renders tree, matrix, or combined view.
 * Drop-in replacement for the legacy passenger-tree component.
 */

interface PassengerTreeProps {
  tree: FlightTree;
  mode?: "combined" | "tree" | "matrix";
  onClose?: () => void;
}

export function PassengerTree({ tree, mode = "combined", onClose }: PassengerTreeProps) {
  return (
    <Card className="shadow-none border-transparent bg-transparent">
      <CardContent className="p-0">
        {mode === "combined" && (
          <div className="mb-2 flex items-center justify-center gap-1">
            <span className="inline-flex items-center gap-1.5 rounded-md border bg-background px-3 py-1 text-xs font-medium">
              <Network className="h-3.5 w-3.5" />
              Tree
            </span>
          </div>
        )}

        {(mode === "combined" || mode === "matrix") && (
          <div className={mode === "combined" ? "rounded-lg border bg-card p-2 mb-4" : ""}>
            <PaxMatrix tree={tree} />
          </div>
        )}

        {(mode === "combined" || mode === "tree") && (
          <div className="rounded-lg border bg-card p-2">
            <PaxTree tree={tree} onClose={onClose} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
