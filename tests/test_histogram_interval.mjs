import test from "node:test";
import assert from "node:assert/strict";

// Import pure utility functions from frontend
import {
  calculateOptimalInterval,
  selectOptimalInterval,
  getIntervalDurationMs,
  MAX_ALLOWED_BUCKETS,
} from "../frontend/src/components/logs/histogramUtils.ts";

test("LogHistogram Interval Selection - Standard Presets", () => {
  assert.equal(selectOptimalInterval(null, null, "15m"), "1m");
  assert.equal(selectOptimalInterval(null, null, "1h"), "2m");
  assert.equal(selectOptimalInterval(null, null, "24h"), "30m");
  assert.equal(selectOptimalInterval(null, null, "7d"), "6h");
  assert.equal(selectOptimalInterval(null, null, "all"), "1d");
});

test("LogHistogram Interval Selection - 15 Days Custom Range", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date("2026-01-16T00:00:00.000Z").toISOString(); // 15 days

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.error, null);
  assert.equal(res.interval, "12h");
  assert.ok(res.projectedBuckets <= MAX_ALLOWED_BUCKETS);
  assert.equal(selectOptimalInterval(start, end, "custom"), "12h");
});

test("LogHistogram Interval Selection - 90 Days Custom Range", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date(new Date(start).getTime() + 90 * 24 * 3600 * 1000).toISOString(); // 90 days

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.error, null);
  assert.equal(res.interval, "1d");
  assert.equal(res.projectedBuckets, 91);
  assert.ok(res.projectedBuckets <= MAX_ALLOWED_BUCKETS);
  assert.equal(selectOptimalInterval(start, end, "custom"), "1d");
});

test("LogHistogram Interval Selection - 365 Days Custom Range", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date(new Date(start).getTime() + 365 * 24 * 3600 * 1000).toISOString(); // 365 days

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.error, null);
  assert.equal(res.interval, "1d");
  assert.equal(res.projectedBuckets, 366);
  assert.ok(res.projectedBuckets <= MAX_ALLOWED_BUCKETS);
  assert.equal(selectOptimalInterval(start, end, "custom"), "1d");
});

test("LogHistogram Interval Selection - 499 Days (Maximum Safe Limit)", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date(new Date(start).getTime() + 499 * 24 * 3600 * 1000).toISOString(); // 499 days

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.error, null);
  assert.equal(res.interval, "1d");
  assert.equal(res.projectedBuckets, 500); // 499 + 1 = 500 buckets
  assert.ok(res.projectedBuckets <= MAX_ALLOWED_BUCKETS);
  assert.equal(selectOptimalInterval(start, end, "custom"), "1d");
});

test("LogHistogram Interval Selection - 500 Days (Exceeds 500 Buckets)", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date(new Date(start).getTime() + 500 * 24 * 3600 * 1000).toISOString(); // 500 days

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.interval, null);
  assert.ok(res.error !== null);
  assert.match(res.error, /exceeding the maximum supported safety limit of 500 buckets/);
  assert.equal(res.projectedBuckets, 501);
  assert.equal(selectOptimalInterval(start, end, "custom"), null);
});

test("LogHistogram Interval Selection - 501 Days (Exceeds 500 Buckets)", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date(new Date(start).getTime() + 501 * 24 * 3600 * 1000).toISOString(); // 501 days

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.interval, null);
  assert.ok(res.error !== null);
  assert.match(res.error, /exceeding the maximum supported safety limit of 500 buckets/);
  assert.equal(res.projectedBuckets, 502);
  assert.equal(selectOptimalInterval(start, end, "custom"), null);
});

test("LogHistogram Interval Selection - 2 Years (730 Days - Exceeds 500 Buckets)", () => {
  const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
  const end = new Date(new Date(start).getTime() + 730 * 24 * 3600 * 1000).toISOString(); // 2 years

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.interval, null);
  assert.ok(res.error !== null);
  assert.match(res.error, /exceeding the maximum supported safety limit of 500 buckets/);
  assert.equal(res.projectedBuckets, 731);
  assert.equal(selectOptimalInterval(start, end, "custom"), null);
});

test("LogHistogram Interval Selection - Invalid inverted range", () => {
  const start = new Date("2026-01-10T00:00:00.000Z").toISOString();
  const end = new Date("2026-01-01T00:00:00.000Z").toISOString();

  const res = calculateOptimalInterval(start, end, "custom");
  assert.equal(res.interval, null);
  assert.match(res.error, /start time must be earlier than end time/);
});

test("LogHistogram getIntervalDurationMs maps all intervals accurately", () => {
  assert.equal(getIntervalDurationMs("1m"), 60000);
  assert.equal(getIntervalDurationMs("2m"), 120000);
  assert.equal(getIntervalDurationMs("5m"), 300000);
  assert.equal(getIntervalDurationMs("10m"), 600000);
  assert.equal(getIntervalDurationMs("15m"), 900000);
  assert.equal(getIntervalDurationMs("30m"), 1800000);
  assert.equal(getIntervalDurationMs("1h"), 3600000);
  assert.equal(getIntervalDurationMs("2h"), 7200000);
  assert.equal(getIntervalDurationMs("6h"), 21600000);
  assert.equal(getIntervalDurationMs("12h"), 43200000);
  assert.equal(getIntervalDurationMs("1d"), 86400000);
});

