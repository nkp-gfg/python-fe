import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const navigation = vi.hoisted(() => ({
  search: "",
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(navigation.search),
  usePathname: () => "/compare",
  useRouter: () => ({
    push() {},
    replace() {},
    prefetch() {},
  }),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

import ComparePage from "@/app/compare/page";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { ToastProvider } from "@/components/ui/toast";

function renderPage(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  navigation.search = "";
});

describe("stored carrier labels", () => {
  it("labels both comparison headings with the airline on the loaded flight status", async () => {
    navigation.search = "flights=2057:LHR:2026-11-20";
    renderPage(<ComparePage />);

    // Aircraft type is rendered only after the dashboard payload is on screen.
    await screen.findByText("32N");

    expect(screen.queryAllByText("GF2057")).toEqual([]);
    expect(screen.getAllByText("BA2057").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps a Gulf Air comparison labeled with the stored GF airline", async () => {
    navigation.search = "flights=3001:BAH:2026-11-20";
    renderPage(<ComparePage />);

    await screen.findByText("32N");

    expect(screen.queryByText("BA3001")).toBeNull();
    expect(screen.getAllByText("GF3001").length).toBeGreaterThanOrEqual(2);
  });

  it("labels the operations header with the airline on the loaded flight status", async () => {
    renderPage(
      <FlightWorkbench
        initialSelection={{
          flightNumber: "2057",
          origin: "LHR",
          date: "2026-11-20",
        }}
      />,
    );

    await screen.findByText("32N");
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent ?? "").toContain("BA2057");
    expect(heading.textContent ?? "").not.toContain("GF2057");
  });
});
