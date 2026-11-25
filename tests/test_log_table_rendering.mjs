import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_PAGE_SIZES,
  DEFAULT_PAGE_SIZE,
  formatUtcTimestamp,
  getLevelBadgeVariant,
  getCellValue,
  calculatePaginationRange,
  getEmptyStateConfig,
} from "../frontend/src/components/logs/tableUtils.ts";

import {
  ALL_COLUMN_IDS,
  AVAILABLE_COLUMNS,
  DEFAULT_COLUMN_PREFERENCES,
  getVisibleColumns,
} from "../frontend/src/components/logs/columnConfig.ts";

test("Table Utils - formatUtcTimestamp converts valid ISO timestamps accurately", () => {
  const ts = "2026-11-16T12:34:56.789Z";
  const formatted = formatUtcTimestamp(ts);
  assert.equal(formatted, "2026-11-16 12:34:56.789");

  // Handles zero milliseconds
  const tsZero = "2026-01-01T00:00:00.000Z";
  assert.equal(formatUtcTimestamp(tsZero), "2026-01-01 00:00:00.000");
});

test("Table Utils - formatUtcTimestamp handles missing and invalid values with fallback", () => {
  assert.equal(formatUtcTimestamp(null), "—");
  assert.equal(formatUtcTimestamp(undefined), "—");
  assert.equal(formatUtcTimestamp(""), "—");
  assert.equal(formatUtcTimestamp("invalid-iso-string"), "—");
});

test("Table Utils - getLevelBadgeVariant maps severities accurately and case-insensitively", () => {
  assert.equal(getLevelBadgeVariant("ERROR"), "critical");
  assert.equal(getLevelBadgeVariant("error"), "critical");
  assert.equal(getLevelBadgeVariant("FATAL"), "critical");
  assert.equal(getLevelBadgeVariant("CRITICAL"), "critical");

  assert.equal(getLevelBadgeVariant("WARN"), "warning");
  assert.equal(getLevelBadgeVariant("warning"), "warning");

  assert.equal(getLevelBadgeVariant("INFO"), "info");
  assert.equal(getLevelBadgeVariant("info"), "info");

  assert.equal(getLevelBadgeVariant("DEBUG"), "neutral");
  assert.equal(getLevelBadgeVariant("TRACE"), "neutral");
  assert.equal(getLevelBadgeVariant("NOTICE"), "neutral");
  assert.equal(getLevelBadgeVariant(null), "neutral");
  assert.equal(getLevelBadgeVariant(undefined), "neutral");
  assert.equal(getLevelBadgeVariant(""), "neutral");
});

test("Table Utils - getCellValue handles canonical fields with present values", () => {
  const mockLog = {
    id: "doc-12345",
    index: "logs-2026-11",
    timestamp: "2026-11-16T10:00:00.000Z",
    service_name: "checkout-service",
    environment: "production",
    log_level: "ERROR",
    message: "Payment gateway timeout after 5000ms",
    host: "worker-node-09",
    trace_id: "trace-abc-123",
    span_id: "span-xyz-789",
    attributes: {},
    raw_log: null,
  };

  assert.equal(getCellValue("timestamp", mockLog), "2026-11-16 10:00:00.000");
  assert.equal(getCellValue("log_level", mockLog), "ERROR");
  assert.equal(getCellValue("service_name", mockLog), "checkout-service");
  assert.equal(getCellValue("message", mockLog), "Payment gateway timeout after 5000ms");
  assert.equal(getCellValue("host", mockLog), "worker-node-09");
  assert.equal(getCellValue("environment", mockLog), "production");
  assert.equal(getCellValue("trace_id", mockLog), "trace-abc-123");
  assert.equal(getCellValue("span_id", mockLog), "span-xyz-789");
  assert.equal(getCellValue("id", mockLog), "doc-12345");
});

test("Table Utils - getCellValue handles missing fields with dash fallback", () => {
  const emptyLog = {
    id: "",
    index: null,
    timestamp: null,
    service_name: null,
    environment: null,
    log_level: null,
    message: null,
    host: null,
    trace_id: null,
    span_id: null,
    attributes: {},
    raw_log: null,
  };

  for (const colId of ALL_COLUMN_IDS) {
    assert.equal(getCellValue(colId, emptyLog), "—", `Column ${colId} must fall back to "—"`);
  }
});

test("Table Utils - getVisibleColumns returns columns respecting visibility and custom order", () => {
  const prefs = {
    visibleColumns: ["timestamp", "service_name", "message"],
    columnOrder: [
      "service_name",
      "timestamp",
      "log_level",
      "message",
      "host",
      "environment",
      "trace_id",
      "span_id",
      "id",
    ],
    messageMode: "truncate",
  };

  const cols = getVisibleColumns(prefs);
  assert.equal(cols.length, 3);
  assert.equal(cols[0].id, "service_name");
  assert.equal(cols[1].id, "timestamp");
  assert.equal(cols[2].id, "message");
});

test("Table Utils - calculatePaginationRange calculates start and end accurately", () => {
  // First page (50 items on page 1 of 120)
  const p1 = calculatePaginationRange(1, 50, 120);
  assert.deepEqual(p1, { start: 1, end: 50 });

  // Second page (items 51-100 of 120)
  const p2 = calculatePaginationRange(2, 50, 120);
  assert.deepEqual(p2, { start: 51, end: 100 });

  // Third partial page (items 101-120 of 120)
  const p3 = calculatePaginationRange(3, 50, 120);
  assert.deepEqual(p3, { start: 101, end: 120 });

  // Exact multiple page boundary (50 items on page 2 of 100)
  const pBoundary = calculatePaginationRange(2, 50, 100);
  assert.deepEqual(pBoundary, { start: 51, end: 100 });

  // Empty results
  const pEmpty = calculatePaginationRange(1, 50, 0);
  assert.deepEqual(pEmpty, { start: 0, end: 0 });

  // Out of bounds page beyond totalEvents
  const pOver = calculatePaginationRange(5, 50, 120);
  assert.deepEqual(pOver, { start: 120, end: 120 });
});

test("Table Utils - DEFAULT_PAGE_SIZES matches backend contract", () => {
  assert.deepEqual(DEFAULT_PAGE_SIZES, [25, 50, 100]);
  assert.equal(DEFAULT_PAGE_SIZE, 50);
});

test("Table Utils - getEmptyStateConfig configures time-constrained empty state correctly", () => {
  // 24h default preset
  const c24h = getEmptyStateConfig("24h");
  assert.equal(c24h.isTimeConstrained, true);
  assert.equal(c24h.title, "No Logs in Selected Time Range");
  assert.equal(c24h.iconName, "schedule");
  assert.equal(c24h.showSwitchToAllTime, true);
  assert.match(c24h.description, /Earlier or historical log records may exist/);

  // Custom preset
  const cCustom = getEmptyStateConfig("custom");
  assert.equal(cCustom.isTimeConstrained, true);
  assert.equal(cCustom.showSwitchToAllTime, true);

  // Other specific presets (15m, 1h, 7d)
  for (const preset of ["15m", "1h", "7d"]) {
    const cp = getEmptyStateConfig(preset);
    assert.equal(cp.isTimeConstrained, true);
    assert.equal(cp.showSwitchToAllTime, true);
  }
});

test("Table Utils - getEmptyStateConfig configures cluster-wide empty state correctly", () => {
  // 'all' preset
  const cAll = getEmptyStateConfig("all");
  assert.equal(cAll.isTimeConstrained, false);
  assert.equal(cAll.title, "No Log Records Found");
  assert.equal(cAll.iconName, "search_off");
  assert.equal(cAll.showSwitchToAllTime, false);
  assert.match(cAll.description, /No matching log documents were returned by OpenSearch/);

  // null or undefined preset
  const cNull = getEmptyStateConfig(null);
  assert.equal(cNull.isTimeConstrained, false);
  assert.equal(cNull.showSwitchToAllTime, false);

  const cUndef = getEmptyStateConfig(undefined);
  assert.equal(cUndef.isTimeConstrained, false);
  assert.equal(cUndef.showSwitchToAllTime, false);
});
