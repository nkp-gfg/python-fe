"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { getApiToken, setApiToken, subscribeApiToken } from "@/lib/auth-token";

export function ApiTokenGate({ children }: { children: ReactNode }) {
  const token = useSyncExternalStore(subscribeApiToken, getApiToken, () => undefined);

  if (token === undefined) return null;
  if (!token) return <SignIn />;
  return <>{children}</>;
}

function SignIn() {
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = value.trim();
    if (!token) return;
    queryClient.removeQueries();
    setApiToken(token);
  }

  return (
    <main id="main-content" className="flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-border bg-card p-6 shadow-sm"
      >
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">FalconEye sign-in</h1>
          <p className="text-sm text-muted-foreground">
            Enter your staff API token to access flight operations data.
          </p>
        </div>
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">API token</span>
          <input
            type="password"
            autoComplete="off"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
        </label>
        <Button type="submit" className="w-full" disabled={!value.trim()}>
          Sign in
        </Button>
      </form>
    </main>
  );
}
