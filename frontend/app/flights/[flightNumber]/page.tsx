"use client";

import { useParams, useSearchParams } from "next/navigation";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";

export default function FlightDashboardPage() {
  const params = useParams();
  const searchParams = useSearchParams();

  return (
    <FlightWorkbench
      initialSelection={{
        flightNumber: params.flightNumber as string,
        origin: searchParams.get("origin") ?? "",
        date: searchParams.get("date") ?? "",
      }}
    />
  );
}
