"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function CompareError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error("[CompareError]", error); }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <h2 className="text-2xl font-semibold text-destructive">Comparison error</h2>
      <p className="text-sm text-muted-foreground max-w-md">{error.message || "An unexpected error occurred loading the comparison view."}</p>
      <div className="flex gap-3">
        <button
          onClick={reset}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          Try again
        </button>
        <Link href="/" className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent transition-colors">
          Back to flights
        </Link>
      </div>
    </div>
  );
}
