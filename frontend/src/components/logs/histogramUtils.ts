/**
 * Pure utility functions for log histogram bucket interval calculations and formatting.
 */

export const MAX_ALLOWED_BUCKETS = 500;

export interface SelectedBucketRange {
  start: string; // ISO string
  end: string;   // ISO string
  count: number;
}

export interface OptimalIntervalResult {
  interval: string | null;
  error: string | null;
  projectedBuckets?: number;
}

export function getIntervalDurationMs(interval: string): number {
  const clean = interval.trim().toLowerCase();
  switch (clean) {
    case "1m":
      return 60 * 1000;
    case "2m":
      return 2 * 60 * 1000;
    case "5m":
      return 5 * 60 * 1000;
    case "10m":
      return 10 * 60 * 1000;
    case "15m":
      return 15 * 60 * 1000;
    case "30m":
      return 30 * 60 * 1000;
    case "1h":
      return 60 * 60 * 1000;
    case "2h":
      return 2 * 60 * 60 * 1000;
    case "6h":
      return 6 * 60 * 60 * 1000;
    case "12h":
      return 12 * 60 * 60 * 1000;
    case "1d":
      return 24 * 60 * 60 * 1000;
    default:
      return 60 * 60 * 1000;
  }
}

export function calculateOptimalInterval(
  startTime?: string | null,
  endTime?: string | null,
  timePreset?: string
): OptimalIntervalResult {
  // Preset handling (presets retain their exact behavior)
  if (timePreset === "15m") return { interval: "1m", error: null, projectedBuckets: 16 };
  if (timePreset === "1h") return { interval: "2m", error: null, projectedBuckets: 31 };
  if (timePreset === "24h") return { interval: "30m", error: null, projectedBuckets: 49 };
  if (timePreset === "7d") return { interval: "6h", error: null, projectedBuckets: 29 };
  if (timePreset === "all") return { interval: "1d", error: null };

  if (startTime && endTime) {
    const s = new Date(startTime).getTime();
    const e = new Date(endTime).getTime();
    if (isNaN(s) || isNaN(e) || s >= e) {
      return {
        interval: null,
        error: "Invalid date range: start time must be earlier than end time.",
      };
    }

    const durationSeconds = (e - s) / 1000;
    const diffHours = durationSeconds / 3600;

    // Pick candidate interval based on range duration
    let candidate = "1d";
    let candidateSeconds = 86400;

    if (diffHours <= 0.5) {
      candidate = "1m";
      candidateSeconds = 60;
    } else if (diffHours <= 2) {
      candidate = "2m";
      candidateSeconds = 120;
    } else if (diffHours <= 6) {
      candidate = "5m";
      candidateSeconds = 300;
    } else if (diffHours <= 24) {
      candidate = "30m";
      candidateSeconds = 1800;
    } else if (diffHours <= 72) {
      candidate = "1h";
      candidateSeconds = 3600;
    } else if (diffHours <= 168) {
      candidate = "6h";
      candidateSeconds = 21600;
    } else if (diffHours <= 720) {
      candidate = "12h";
      candidateSeconds = 43200;
    } else {
      candidate = "1d";
      candidateSeconds = 86400;
    }

    // Step up to coarser supported intervals if candidate exceeds 500 buckets
    let projectedBuckets = Math.ceil(durationSeconds / candidateSeconds) + 1;
    if (projectedBuckets > MAX_ALLOWED_BUCKETS) {
      const fallbackIntervals: Array<[string, number]> = [
        ["2m", 120],
        ["5m", 300],
        ["10m", 600],
        ["15m", 900],
        ["30m", 1800],
        ["1h", 3600],
        ["2h", 7200],
        ["6h", 21600],
        ["12h", 43200],
        ["1d", 86400],
      ];
      for (const [intName, intSec] of fallbackIntervals) {
        if (intSec > candidateSeconds) {
          const pb = Math.ceil(durationSeconds / intSec) + 1;
          if (pb <= MAX_ALLOWED_BUCKETS) {
            candidate = intName;
            candidateSeconds = intSec;
            projectedBuckets = pb;
            break;
          }
        }
      }
    }

    // Check if even the maximum supported interval ("1d") exceeds safety limit
    const dailyProjected = Math.ceil(durationSeconds / 86400) + 1;
    if (dailyProjected > MAX_ALLOWED_BUCKETS) {
      const days = Math.round(durationSeconds / 86400);
      return {
        interval: null,
        error: `Selected custom range spans ${days} days and requires ${dailyProjected} daily buckets, exceeding the maximum supported safety limit of ${MAX_ALLOWED_BUCKETS} buckets. Please select a time range under 499 days.`,
        projectedBuckets: dailyProjected,
      };
    }

    return {
      interval: candidate,
      error: null,
      projectedBuckets,
    };
  }

  return { interval: "30m", error: null };
}

export function selectOptimalInterval(
  startTime?: string | null,
  endTime?: string | null,
  timePreset?: string
): string | null {
  return calculateOptimalInterval(startTime, endTime, timePreset).interval;
}

export function formatBucketLabel(isoStr: string, interval: string): string {
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    const isDaily = interval.toLowerCase().endsWith("d");
    if (isDaily) {
      return d.toISOString().substring(5, 10); // MM-DD
    }
    return d.toISOString().substring(11, 16); // HH:mm
  } catch {
    return isoStr;
  }
}

export function formatFullUtc(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.toISOString().replace("T", " ").substring(0, 19) + " UTC";
  } catch {
    return isoStr;
  }
}

