// Sabre clock parsing. ACS times use Time_hhmma ("09:50PM") and are stored
// joined to the date ("2026-03-18T09:50PM"); VerifyFlightDetails uses a
// 24-hour dateTime whose year and seconds are optional ("03-18T21:50").
// Never slice these at fixed offsets: the length and meridian vary.

export interface SabreDateTime {
  year?: number;
  month?: number;
  day?: number;
  /** 0–23 */
  hours: number;
  minutes: number;
  /** Minutes east of UTC, when the value carries an explicit offset */
  offsetMinutes?: number;
}

const SABRE_DATETIME_RE =
  /^(?:(?:(\d{4})-)?(\d{2})-(\d{2})T)?(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*([AP]M)?\s*(Z|[+-]\d{2}:?\d{2})?$/i;

function textOf(value: unknown): string | null {
  if (typeof value === "string") return value.trim();
  if (value && typeof value === "object" && "#text" in value) {
    const text = (value as { "#text"?: unknown })["#text"];
    return typeof text === "string" ? text.trim() : null;
  }
  return null;
}

/** Parse a Sabre date-time or bare time; returns null when no valid clock is present. */
export function parseSabreDateTime(value: unknown): SabreDateTime | null {
  const text = textOf(value);
  if (!text) return null;
  const m = SABRE_DATETIME_RE.exec(text);
  if (!m) return null;

  const [, yearStr, monthStr, dayStr, hourStr, minuteStr, secondStr, meridian, offset] = m;
  let hours = Number(hourStr);
  const minutes = Number(minuteStr);
  if (minutes > 59) return null;
  if (secondStr !== undefined && Number(secondStr) > 59) return null;

  if (meridian) {
    if (hours > 12) return null;
    const pm = meridian.toUpperCase() === "PM";
    hours = (hours % 12) + (pm ? 12 : 0);
  } else if (hours > 23) {
    return null;
  }

  const result: SabreDateTime = { hours, minutes };

  if (monthStr !== undefined) {
    const month = Number(monthStr);
    const day = Number(dayStr);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    result.month = month;
    result.day = day;
    if (yearStr !== undefined) result.year = Number(yearStr);
  }

  if (offset) {
    if (offset.toUpperCase() === "Z") {
      result.offsetMinutes = 0;
    } else {
      const sign = offset[0] === "-" ? -1 : 1;
      const digits = offset.slice(1).replace(":", "");
      result.offsetMinutes = sign * (Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2, 4)));
    }
  }

  return result;
}

/** 24-hour "HH:mm" for a Sabre date-time or bare time, or null when it has no valid clock. */
export function formatClock24(value: unknown): string | null {
  const parsed = parseSabreDateTime(value);
  if (!parsed) return null;
  return `${String(parsed.hours).padStart(2, "0")}:${String(parsed.minutes).padStart(2, "0")}`;
}

function absoluteMinutes(t: SabreDateTime, fallbackYear: number): number {
  const year = t.year ?? fallbackYear;
  const ms = Date.UTC(year, (t.month ?? 1) - 1, t.day ?? 1, t.hours, t.minutes);
  return ms / 60000 - (t.offsetMinutes ?? 0);
}

/**
 * Minutes from `from` to `to`. Uses the dates when both carry one, so an
 * estimate past midnight is a real difference; when neither has a date the
 * clocks are compared on the same day. Returns null if either is unparseable
 * or only one side has a date.
 */
export function sabreMinutesBetween(from: unknown, to: unknown): number | null {
  const a = parseSabreDateTime(from);
  const b = parseSabreDateTime(to);
  if (!a || !b) return null;

  const aDated = a.month !== undefined;
  const bDated = b.month !== undefined;
  if (aDated !== bDated) return null;

  const fallbackYear = a.year ?? b.year ?? 2000;
  return Math.round(absoluteMinutes(b, fallbackYear) - absoluteMinutes(a, fallbackYear));
}

/** Convert a Sabre date-time to "YYYY-MM-DDTHH:mm:00" on its own date, or null if it lacks a full date and clock. */
export function toIsoLocalDateTime(value: unknown): string | null {
  const parsed = parseSabreDateTime(value);
  if (!parsed || parsed.year === undefined || parsed.month === undefined || parsed.day === undefined) {
    return null;
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${parsed.year}-${pad(parsed.month)}-${pad(parsed.day)}T${pad(parsed.hours)}:${pad(parsed.minutes)}:00`;
}
