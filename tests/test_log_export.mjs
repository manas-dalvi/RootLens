import test from "node:test";
import assert from "node:assert/strict";

import {
  escapeCsvField,
  getLogColumnValue,
  serializeLogsToCsv,
  serializeLogsToJson,
  generateExportFilename,
  fetchAllFilteredLogs,
  MAX_EXPORT_LIMIT,
  EXPORT_BATCH_SIZE,
} from "../frontend/src/lib/exportUtils.ts";

// Sample mock log document
const createMockLog = (id, overrides = {}) => ({
  id,
  index: "rootlens-logs-2026.10",
  timestamp: "2026-10-14T11:35:00Z",
  service_name: "checkout-service",
  environment: "production",
  log_level: "ERROR",
  message: "Connection pool exhausted to primary DB",
  host: "worker-prod-01",
  trace_id: "trace-abc-123",
  span_id: "span-def-456",
  attributes: { user_id: "usr-99", retry_count: 3 },
  raw_log: null,
  ...overrides,
});

test("Export Utils - escapeCsvField handles RFC 4180 escaping rules", () => {
  // 1. Clean strings without delimiters
  assert.equal(escapeCsvField("simple_value"), "simple_value");
  assert.equal(escapeCsvField("checkout-service"), "checkout-service");

  // 2. Null and undefined
  assert.equal(escapeCsvField(null), "");
  assert.equal(escapeCsvField(undefined), "");

  // 3. Values containing commas
  assert.equal(escapeCsvField("error, timeout occurred"), '"error, timeout occurred"');

  // 4. Values containing double quotes
  assert.equal(escapeCsvField('Failed with "connection refused"'), '"Failed with ""connection refused"""');

  // 5. Values containing newlines
  assert.equal(escapeCsvField("line 1\nline 2"), '"line 1\nline 2"');
  assert.equal(escapeCsvField("line 1\r\nline 2"), '"line 1\r\nline 2"');

  // 6. Values containing comma, quotes, and newlines together
  const complex = 'Error, code: "500"\nDetails: retry';
  assert.equal(escapeCsvField(complex), '"Error, code: ""500""\nDetails: retry"');

  // 7. Structured objects/arrays
  const obj = { host: "node-1", code: 500 };
  const escapedObj = escapeCsvField(obj);
  assert.ok(escapedObj.startsWith('"') && escapedObj.endsWith('"'));
  assert.ok(escapedObj.includes('""host"":""node-1""'));
});

test("Export Utils - serializeLogsToCsv includes UTF-8 BOM and formats headers and rows", () => {
  const logs = [
    createMockLog("log-1", { message: "Normal message" }),
    createMockLog("log-2", { message: 'Message with "quotes" and, commas' }),
  ];

  const csv = serializeLogsToCsv(logs);

  // 1. Begins with UTF-8 BOM (\uFEFF)
  assert.equal(csv.charCodeAt(0), 0xfeff, "First character must be UTF-8 BOM");

  // Strip BOM for line parsing
  const content = csv.slice(1);
  const lines = content.split("\r\n");

  // 2. Header line contains all canonical columns by default
  assert.equal(
    lines[0],
    "timestamp,log_level,service_name,message,host,environment,trace_id,span_id,id"
  );

  // 3. Two data rows
  assert.equal(lines.length, 3);
  assert.ok(lines[1].includes("log-1"));
  assert.ok(lines[2].includes('""quotes"" and, commas'));
});

test("Export Utils - serializeLogsToCsv respects user column visibility and custom order", () => {
  const logs = [createMockLog("log-1")];
  const customPrefs = {
    visibleColumns: ["service_name", "timestamp", "message"],
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

  const csv = serializeLogsToCsv(logs, customPrefs);
  const content = csv.slice(1);
  const lines = content.split("\r\n");

  // Headers must match visible columns in custom order
  assert.equal(lines[0], "service_name,timestamp,message");

  // Data row must correspond to the 3 columns
  assert.equal(
    lines[1],
    'checkout-service,2026-10-14T11:35:00Z,Connection pool exhausted to primary DB'
  );
});

test("Export Utils - serializeLogsToJson formats complete canonical log documents", () => {
  const logs = [createMockLog("log-1"), createMockLog("log-2")];
  const jsonStr = serializeLogsToJson(logs);

  const parsed = JSON.parse(jsonStr);
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].id, "log-1");
  assert.equal(parsed[0].service_name, "checkout-service");
  assert.deepEqual(parsed[0].attributes, { user_id: "usr-99", retry_count: 3 });
  assert.equal(parsed[1].id, "log-2");
});

test("Export Utils - generateExportFilename builds deterministic and sanitized filenames", () => {
  const fixedDate = new Date("2026-11-17T14:30:45Z");

  // 1. Current page JSON export with service
  const f1 = generateExportFilename({
    scope: "page",
    format: "json",
    service: "Checkout Service",
    totalCount: 25,
    page: 2,
    timestamp: fixedDate,
  });
  assert.match(f1, /^rootlens-logs-checkout_service-p2-25events-\d{8}-\d{6}\.json$/);

  // 2. All results CSV export with no service
  const f2 = generateExportFilename({
    scope: "all",
    format: "csv",
    totalCount: 38,
    timestamp: fixedDate,
  });
  assert.match(f2, /^rootlens-logs-all-services-all-38events-\d{8}-\d{6}\.csv$/);
});

test("Export Utils - fetchAllFilteredLogs handles single-page export with progress", async () => {
  const mockLogs = Array.from({ length: 38 }, (_, i) =>
    createMockLog(`doc-${i + 1}`)
  );

  const mockFetch = async (params) => {
    assert.equal(params.page, 1);
    assert.equal(params.page_size, EXPORT_BATCH_SIZE);
    return {
      items: mockLogs,
      page: 1,
      page_size: EXPORT_BATCH_SIZE,
      total: 38,
      total_pages: 1,
    };
  };

  const progressUpdates = [];
  const result = await fetchAllFilteredLogs(
    { service: "checkout-service" },
    38,
    mockFetch,
    (p) => progressUpdates.push(p)
  );

  assert.equal(result.logs.length, 38);
  assert.equal(result.totalActual, 38);
  assert.equal(result.countChanged, false);
  assert.equal(progressUpdates.length, 1);
  assert.equal(progressUpdates[0].percent, 100);
});

test("Export Utils - fetchAllFilteredLogs handles multi-page sequential batches", async () => {
  // Total 250 items across 3 pages: 100, 100, 50
  const mockFetch = async (params) => {
    const page = params.page;
    const start = (page - 1) * EXPORT_BATCH_SIZE;
    const count = page === 3 ? 50 : 100;
    const items = Array.from({ length: count }, (_, i) =>
      createMockLog(`doc-${start + i + 1}`)
    );
    return {
      items,
      page,
      page_size: EXPORT_BATCH_SIZE,
      total: 250,
      total_pages: 3,
    };
  };

  const progressUpdates = [];
  const result = await fetchAllFilteredLogs(
    {},
    250,
    mockFetch,
    (p) => progressUpdates.push(p)
  );

  assert.equal(result.logs.length, 250);
  assert.equal(progressUpdates.length, 3);
  assert.equal(progressUpdates[0].fetchedRecords, 100);
  assert.equal(progressUpdates[1].fetchedRecords, 200);
  assert.equal(progressUpdates[2].fetchedRecords, 250);
  assert.equal(progressUpdates[2].percent, 100);
});

test("Export Utils - fetchAllFilteredLogs respects MAX_EXPORT_LIMIT boundary (10,000)", async () => {
  let callCount = 0;
  const mockFetch = async (params) => {
    callCount++;
    return {
      items: Array.from({ length: EXPORT_BATCH_SIZE }, (_, i) =>
        createMockLog(`doc-${params.page}-${i}`)
      ),
      page: params.page,
      page_size: EXPORT_BATCH_SIZE,
      total: 50000, // Total in OpenSearch is 50k
      total_pages: 500,
    };
  };

  // We request export on a dataset with 50,000 records
  const result = await fetchAllFilteredLogs({}, 50000, mockFetch);

  // Must be strictly capped at 10,000 (100 calls * 100 batch size)
  assert.equal(callCount, MAX_EXPORT_LIMIT / EXPORT_BATCH_SIZE);
  assert.equal(result.logs.length, MAX_EXPORT_LIMIT);
});

test("Export Utils - fetchAllFilteredLogs handles empty results safely", async () => {
  let called = false;
  const mockFetch = async () => {
    called = true;
    return { items: [], page: 1, page_size: 100, total: 0, total_pages: 0 };
  };

  const result = await fetchAllFilteredLogs({}, 0, mockFetch);
  assert.equal(called, false, "Should not invoke fetch for 0 records");
  assert.deepEqual(result.logs, []);
  assert.equal(result.totalActual, 0);
});

test("Export Utils - fetchAllFilteredLogs detects result count changes during export", async () => {
  let callIndex = 0;
  const mockFetch = async (params) => {
    callIndex++;
    const count = callIndex === 1 ? 100 : 50;
    return {
      items: Array.from({ length: count }, (_, i) => createMockLog(`doc-${callIndex}-${i}`)),
      page: params.page,
      page_size: EXPORT_BATCH_SIZE,
      total: callIndex === 1 ? 150 : 180, // Total shifted while exporting page 2
      total_pages: 2,
    };
  };

  const result = await fetchAllFilteredLogs({}, 150, mockFetch);
  assert.equal(result.countChanged, true, "Must flag countChanged warning");
});

test("Export Utils - fetchAllFilteredLogs supports cancellation via AbortSignal", async () => {
  const controller = new AbortController();

  let callCount = 0;
  const mockFetch = async (params, signal) => {
    callCount++;
    if (callCount === 2) {
      controller.abort();
    }
    return {
      items: Array.from({ length: 100 }, (_, i) => createMockLog(`doc-${i}`)),
      page: params.page,
      page_size: EXPORT_BATCH_SIZE,
      total: 500,
      total_pages: 5,
    };
  };

  await assert.rejects(
    async () => {
      await fetchAllFilteredLogs({}, 500, mockFetch, undefined, controller.signal);
    },
    (err) => err.name === "AbortError"
  );

  assert.equal(callCount, 2);
});

test("Export Utils - fetchAllFilteredLogs propagates underlying network errors", async () => {
  const mockFetch = async () => {
    throw new Error("503 Service Unavailable: OpenSearch offline");
  };

  await assert.rejects(
    async () => {
      await fetchAllFilteredLogs({}, 100, mockFetch);
    },
    /OpenSearch offline/
  );
});
