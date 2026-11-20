import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BoardingProgress } from "@/components/dashboard/boarding-progress";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

export function BoardingProgressCardHarness() {
  return (
    <QueryClientProvider client={queryClient}>
      <BoardingProgress flightNumber="2057" origin="BAH" date="2026-03-19" />
    </QueryClientProvider>
  );
}
