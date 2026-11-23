import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import DataAuditPage from "../app/data-audit/page";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, gcTime: 0 } },
});

export function resetAuditQueries() {
  queryClient.clear();
}

export function DataAuditHarness() {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <QueryClientProvider client={queryClient}>
        <DataAuditPage />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
