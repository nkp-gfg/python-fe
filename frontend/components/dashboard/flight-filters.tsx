"use client";

import { CalendarDays, Filter, X } from "lucide-react";
import { format } from "date-fns";
import { cn } from "@/lib/utils";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

interface QuickDateItem {
  key: string;
  label: string;
  count: number;
}

export interface FlightFiltersProps {
  dateFilter: string;
  setDateFilter: (v: string) => void;
  statusFilter: string;
  setStatusFilter: (v: string) => void;
  calendarOpen: boolean;
  setCalendarOpen: (v: boolean) => void;
  calendarSelectedDate: Date | undefined;
  availableDateSet: Set<string>;
  availableStatuses: string[];
  quickDateFilterItems: QuickDateItem[];
}

export function FlightFilters({
  dateFilter,
  setDateFilter,
  statusFilter,
  setStatusFilter,
  calendarOpen,
  setCalendarOpen,
  calendarSelectedDate,
  availableDateSet,
  availableStatuses,
  quickDateFilterItems,
}: FlightFiltersProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
          <PopoverTrigger asChild>
            <button
              className={cn(
                "flex-1 flex items-center gap-1.5 rounded-md border py-1.5 px-2 text-xs shadow-sm transition-colors",
                dateFilter !== "all"
                  ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                  : "border-input bg-background text-muted-foreground"
              )}
            >
              <CalendarDays className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{dateFilter === "all" ? "All dates" : dateFilter}</span>
              {dateFilter !== "all" && (
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => { e.stopPropagation(); setDateFilter("all"); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); setDateFilter("all"); } }}
                  className="ml-auto rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                  aria-label="Clear date filter"
                >
                  <X className="h-3 w-3" />
                </span>
              )}
            </button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="single"
              selected={calendarSelectedDate}
              onSelect={(day) => {
                if (day) {
                  setDateFilter(format(day, "yyyy-MM-dd"));
                } else {
                  setDateFilter("all");
                }
                setCalendarOpen(false);
              }}
              disabled={(day) => !availableDateSet.has(format(day, "yyyy-MM-dd"))}
              defaultMonth={calendarSelectedDate ?? new Date()}
            />
            {dateFilter !== "all" && (
              <div className="border-t px-3 py-2">
                <button
                  onClick={() => { setDateFilter("all"); setCalendarOpen(false); }}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="h-3 w-3" />
                  Clear date filter
                </button>
              </div>
            )}
          </PopoverContent>
        </Popover>
        <div className="relative flex-1">
          <Filter className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className={cn(
              "w-full appearance-none rounded-md border py-1.5 pl-7 pr-6 text-xs shadow-sm cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              statusFilter !== "all"
                ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                : "border-input bg-background"
            )}
          >
            <option value="all">All statuses</option>
            {availableStatuses.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          {statusFilter !== "all" && (
            <button
              onClick={() => setStatusFilter("all")}
              className="absolute right-1 top-1.5 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
              aria-label="Clear status filter"
            >
              <X className="h-3 w-3 text-blue-500" />
            </button>
          )}
        </div>
      </div>
      {quickDateFilterItems.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {quickDateFilterItems.map((item) => {
            const isActive = dateFilter === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setDateFilter(item.key)}
                className={cn(
                  "rounded-full border px-2 py-1 text-[11px] font-medium transition-colors",
                  isActive
                    ? "border-blue-500 bg-blue-500/15 text-blue-600 dark:text-blue-400"
                    : "border-input bg-background text-muted-foreground hover:text-foreground hover:bg-accent"
                )}
                aria-label={`${item.label} quick filter (${item.count} flights)`}
              >
                {item.label} ({item.count})
              </button>
            );
          })}
        </div>
      )}
      {(dateFilter !== "all" || statusFilter !== "all") && (
        <button
          onClick={() => { setDateFilter("all"); setStatusFilter("all"); }}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="h-3 w-3" />
          Clear all filters
        </button>
      )}
    </div>
  );
}
