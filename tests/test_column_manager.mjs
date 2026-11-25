import test from "node:test";
import assert from "node:assert/strict";

import {
  ALL_COLUMN_IDS,
  AVAILABLE_COLUMNS,
  DEFAULT_COLUMN_PREFERENCES,
  STORAGE_KEY,
  toggleColumnVisibility,
  reorderColumn,
  filterColumnDefinitions,
  loadStoredPreferences,
  saveStoredPreferences,
  resetStoredPreferences,
} from "../frontend/src/components/logs/columnConfig.ts";

test("ColumnManager - Essential column protection", () => {
  const initialPrefs = {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  };

  // Attempt to toggle timestamp off
  const resTimestamp = toggleColumnVisibility(initialPrefs, "timestamp");
  assert.ok(resTimestamp.visibleColumns.includes("timestamp"), "Timestamp must remain visible");

  // Attempt to toggle message off
  const resMessage = toggleColumnVisibility(initialPrefs, "message");
  assert.ok(resMessage.visibleColumns.includes("message"), "Message must remain visible");
});

test("ColumnManager - Visibility toggling for optional columns", () => {
  const initialPrefs = {
    visibleColumns: ["timestamp", "log_level", "service_name", "message", "host"],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  };

  // Toggle host off (currently visible)
  const withoutHost = toggleColumnVisibility(initialPrefs, "host");
  assert.equal(withoutHost.visibleColumns.includes("host"), false);
  assert.equal(withoutHost.visibleColumns.length, 4);

  // Toggle environment on (currently hidden)
  const withEnv = toggleColumnVisibility(withoutHost, "environment");
  assert.equal(withEnv.visibleColumns.includes("environment"), true);
  assert.equal(withEnv.visibleColumns.length, 5);

  // Verify visible columns are sorted according to columnOrder
  const expectedOrder = withoutHost.columnOrder.filter((id) =>
    withEnv.visibleColumns.includes(id)
  );
  assert.deepEqual(withEnv.visibleColumns, expectedOrder);

  // Toggle trace_id and span_id on
  const withTraces = toggleColumnVisibility(
    toggleColumnVisibility(withEnv, "trace_id"),
    "span_id"
  );
  assert.ok(withTraces.visibleColumns.includes("trace_id"));
  assert.ok(withTraces.visibleColumns.includes("span_id"));
});

test("ColumnManager - Reordering columns up and down within bounds", () => {
  const initialPrefs = {
    visibleColumns: ["timestamp", "log_level", "service_name", "message", "host"],
    columnOrder: [
      "timestamp",
      "log_level",
      "service_name",
      "message",
      "host",
      "environment",
      "trace_id",
      "span_id",
      "id",
    ],
    messageMode: "truncate",
  };

  // Move "log_level" (index 1) up -> swaps with "timestamp" (index 0)
  const movedUp = reorderColumn(initialPrefs, "log_level", "up");
  assert.equal(movedUp.columnOrder[0], "log_level");
  assert.equal(movedUp.columnOrder[1], "timestamp");
  // visibleColumns must reflect the new columnOrder
  assert.equal(movedUp.visibleColumns[0], "log_level");
  assert.equal(movedUp.visibleColumns[1], "timestamp");

  // Attempt to move top item ("log_level") up -> bounds check, no change
  const topUp = reorderColumn(movedUp, "log_level", "up");
  assert.deepEqual(topUp.columnOrder, movedUp.columnOrder);

  // Move "log_level" back down -> swaps back with "timestamp"
  const movedDown = reorderColumn(movedUp, "log_level", "down");
  assert.equal(movedDown.columnOrder[0], "timestamp");
  assert.equal(movedDown.columnOrder[1], "log_level");

  // Attempt to move bottom item ("id") down -> bounds check, no change
  const bottomDown = reorderColumn(initialPrefs, "id", "down");
  assert.deepEqual(bottomDown.columnOrder, initialPrefs.columnOrder);

  // Non-existent column ID check
  const nonExistent = reorderColumn(initialPrefs, "unknown_col", "up");
  assert.deepEqual(nonExistent.columnOrder, initialPrefs.columnOrder);
});

test("ColumnManager - Search and filtering column definitions", () => {
  // Query matching label
  const filterByLabel = filterColumnDefinitions(AVAILABLE_COLUMNS, "environment");
  assert.equal(filterByLabel.length, 1);
  assert.equal(filterByLabel[0].id, "environment");

  // Query matching description
  const filterByDesc = filterColumnDefinitions(AVAILABLE_COLUMNS, "microservice");
  assert.equal(filterByDesc.length, 1);
  assert.equal(filterByDesc[0].id, "service_name");

  // Query matching canonical ID
  const filterById = filterColumnDefinitions(AVAILABLE_COLUMNS, "trace_id");
  assert.equal(filterById.length, 1);
  assert.equal(filterById[0].id, "trace_id");

  // Case-insensitive query
  const caseInsensitive = filterColumnDefinitions(AVAILABLE_COLUMNS, "tImE");
  assert.equal(caseInsensitive.length, 1);
  assert.equal(caseInsensitive[0].id, "timestamp");

  // Empty or whitespace query returns all 9 columns
  assert.equal(filterColumnDefinitions(AVAILABLE_COLUMNS, "").length, 9);
  assert.equal(filterColumnDefinitions(AVAILABLE_COLUMNS, "   ").length, 9);

  // No match
  assert.equal(filterColumnDefinitions(AVAILABLE_COLUMNS, "nonexistent_field").length, 0);
});

test("ColumnManager - Reset to defaults restores initial configuration and clears storage", () => {
  const mockStorage = new Map();
  const originalWindow = globalThis.window;

  globalThis.window = {
    localStorage: {
      getItem: (key) => mockStorage.get(key) || null,
      setItem: (key, val) => mockStorage.set(key, String(val)),
      removeItem: (key) => mockStorage.delete(key),
    },
  };

  try {
    // Save customized preferences
    const customPrefs = {
      visibleColumns: ["timestamp", "message", "trace_id"],
      columnOrder: [
        "message",
        "timestamp",
        "trace_id",
        "log_level",
        "service_name",
        "host",
        "environment",
        "span_id",
        "id",
      ],
      messageMode: "wrap",
    };

    saveStoredPreferences(customPrefs);
    assert.ok(mockStorage.has(STORAGE_KEY));

    // Reset
    const defaults = resetStoredPreferences();
    assert.equal(mockStorage.has(STORAGE_KEY), false, "Storage key must be cleared");
    assert.deepEqual(defaults.visibleColumns, [...DEFAULT_COLUMN_PREFERENCES.visibleColumns]);
    assert.deepEqual(defaults.columnOrder, [...DEFAULT_COLUMN_PREFERENCES.columnOrder]);
    assert.equal(defaults.messageMode, "truncate");
  } finally {
    globalThis.window = originalWindow;
  }
});

test("ColumnManager - Preference persistence on toggle and reorder", () => {
  const mockStorage = new Map();
  const originalWindow = globalThis.window;

  globalThis.window = {
    localStorage: {
      getItem: (key) => mockStorage.get(key) || null,
      setItem: (key, val) => mockStorage.set(key, String(val)),
      removeItem: (key) => mockStorage.delete(key),
    },
  };

  try {
    let current = {
      visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
      columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
      messageMode: "truncate",
    };

    // 1. Toggle visibility of environment
    current = toggleColumnVisibility(current, "environment");
    saveStoredPreferences(current);

    const loaded1 = loadStoredPreferences();
    assert.ok(loaded1.visibleColumns.includes("environment"));

    // 2. Reorder service_name up
    current = reorderColumn(current, "service_name", "up");
    saveStoredPreferences(current);

    const loaded2 = loadStoredPreferences();
    assert.equal(loaded2.columnOrder[1], "service_name");
  } finally {
    globalThis.window = originalWindow;
  }
});
