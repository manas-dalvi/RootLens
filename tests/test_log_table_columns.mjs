import test from "node:test";
import assert from "node:assert/strict";

import {
  ALL_COLUMN_IDS,
  ESSENTIAL_COLUMN_IDS,
  AVAILABLE_COLUMNS,
  DEFAULT_COLUMN_PREFERENCES,
  STORAGE_KEY,
  isColumnId,
  validatePreferences,
  loadStoredPreferences,
  saveStoredPreferences,
  resetStoredPreferences,
} from "../frontend/src/components/logs/columnConfig.ts";

test("Canonical Column IDs - Matches LogDocumentResponse exactly", () => {
  const expectedCanonical = [
    "timestamp",
    "log_level",
    "service_name",
    "message",
    "host",
    "environment",
    "trace_id",
    "span_id",
    "id",
  ];

  assert.deepEqual([...ALL_COLUMN_IDS], expectedCanonical);
  assert.equal(ALL_COLUMN_IDS.length, 9);

  // Validate isColumnId guard
  for (const id of expectedCanonical) {
    assert.equal(isColumnId(id), true);
  }
  assert.equal(isColumnId("level"), false, "Alias 'level' must not be canonical");
  assert.equal(isColumnId("service"), false, "Alias 'service' must not be canonical");
  assert.equal(isColumnId(""), false);
  assert.equal(isColumnId(null), false);
  assert.equal(isColumnId(undefined), false);
  assert.equal(isColumnId(123), false);
});

test("Column Definitions - Metadata integrity and essential status", () => {
  assert.equal(AVAILABLE_COLUMNS.length, 9);

  const columnMap = new Map(AVAILABLE_COLUMNS.map((col) => [col.id, col]));

  // Verify all canonical IDs have a definition
  for (const id of ALL_COLUMN_IDS) {
    const def = columnMap.get(id);
    assert.ok(def, `Column ${id} must have definition`);
    assert.ok(def.label.length > 0, `Column ${id} must have label`);
    assert.ok(def.description.length > 0, `Column ${id} must have description`);
    assert.ok(def.minWidth > 0, `Column ${id} must have positive minWidth`);
  }

  // Verify essential columns
  const essential = AVAILABLE_COLUMNS.filter((c) => c.isEssential).map((c) => c.id);
  assert.deepEqual(essential.sort(), ["message", "timestamp"].sort());
  assert.deepEqual([...ESSENTIAL_COLUMN_IDS].sort(), ["message", "timestamp"].sort());
});

test("Default Preferences - Visible, hidden and message mode", () => {
  assert.deepEqual(DEFAULT_COLUMN_PREFERENCES.visibleColumns, [
    "timestamp",
    "log_level",
    "service_name",
    "message",
    "host",
  ]);

  assert.deepEqual(DEFAULT_COLUMN_PREFERENCES.columnOrder, [
    "timestamp",
    "log_level",
    "service_name",
    "message",
    "host",
    "environment",
    "trace_id",
    "span_id",
    "id",
  ]);

  assert.equal(DEFAULT_COLUMN_PREFERENCES.messageMode, "truncate");
});

test("Validation - Essential column protection", () => {
  // Attempt to hide all columns or hide essential columns
  const input = {
    visibleColumns: ["service_name", "host"],
    columnOrder: ["service_name", "host"],
    messageMode: "truncate",
  };

  const validated = validatePreferences(input);

  // Both timestamp and message must be restored
  assert.ok(validated.visibleColumns.includes("timestamp"));
  assert.ok(validated.visibleColumns.includes("message"));
  assert.ok(validated.visibleColumns.includes("service_name"));
  assert.ok(validated.visibleColumns.includes("host"));
});

test("Validation - Invalid, unknown and alias IDs stripped safely", () => {
  const input = {
    visibleColumns: [
      "level", // alias
      "service", // alias
      "timestamp",
      "unknown_prop",
      "service_name",
      "__proto__",
      "message",
    ],
    columnOrder: [
      "invalid_id",
      "timestamp",
      "service_name",
      "message",
    ],
    messageMode: "truncate",
  };

  const validated = validatePreferences(input);

  assert.ok(!validated.visibleColumns.includes("level"));
  assert.ok(!validated.visibleColumns.includes("service"));
  assert.ok(!validated.visibleColumns.includes("unknown_prop"));
  assert.ok(!validated.visibleColumns.includes("__proto__"));
  assert.ok(!validated.columnOrder.includes("invalid_id"));

  // Check that all valid columns remain
  assert.ok(validated.visibleColumns.includes("timestamp"));
  assert.ok(validated.visibleColumns.includes("service_name"));
  assert.ok(validated.visibleColumns.includes("message"));
});

test("Validation - Duplicate IDs deduplicated while preserving order", () => {
  const input = {
    visibleColumns: [
      "host",
      "timestamp",
      "host",
      "message",
      "timestamp",
      "service_name",
      "service_name",
    ],
    columnOrder: [
      "host",
      "timestamp",
      "host",
      "service_name",
      "message",
    ],
    messageMode: "truncate",
  };

  const validated = validatePreferences(input);

  // No duplicates in visibleColumns
  const visibleSet = new Set(validated.visibleColumns);
  assert.equal(visibleSet.size, validated.visibleColumns.length);

  // No duplicates in columnOrder
  const orderSet = new Set(validated.columnOrder);
  assert.equal(orderSet.size, validated.columnOrder.length);
  assert.equal(validated.columnOrder.length, 9, "All 9 canonical columns must exist in columnOrder");
});

test("Validation - Missing column-order entries reconciled cleanly", () => {
  const input = {
    visibleColumns: ["timestamp", "message"],
    columnOrder: ["message", "timestamp"], // only 2 specified
    messageMode: "truncate",
  };

  const validated = validatePreferences(input);

  assert.equal(validated.columnOrder.length, 9);
  assert.equal(validated.columnOrder[0], "message");
  assert.equal(validated.columnOrder[1], "timestamp");
  // Omitted columns should be appended
  for (const id of ALL_COLUMN_IDS) {
    assert.ok(validated.columnOrder.includes(id));
  }
});

test("Validation - Corrupted JSON and non-object inputs fall back to safe defaults", () => {
  assert.deepEqual(validatePreferences(null), {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  });

  assert.deepEqual(validatePreferences(undefined), {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  });

  assert.deepEqual(validatePreferences("random string"), {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  });

  assert.deepEqual(validatePreferences([1, 2, 3]), {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  });

  assert.deepEqual(validatePreferences(42), {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  });
});

test("Validation - Incomplete and outdated preference objects repaired", () => {
  // Empty object
  const res1 = validatePreferences({});
  assert.deepEqual(res1.visibleColumns, [...DEFAULT_COLUMN_PREFERENCES.visibleColumns]);
  assert.deepEqual(res1.columnOrder, [...DEFAULT_COLUMN_PREFERENCES.columnOrder]);
  assert.equal(res1.messageMode, "truncate");

  // Missing messageMode defaults to truncate
  const res2 = validatePreferences({
    visibleColumns: ["timestamp", "message"],
    columnOrder: ["timestamp", "message"],
    messageMode: "invalid_mode",
  });
  assert.equal(res2.messageMode, "truncate");

  // Valid wrap mode preserved
  const res3 = validatePreferences({
    visibleColumns: ["timestamp", "message"],
    columnOrder: ["timestamp", "message"],
    messageMode: "wrap",
  });
  assert.equal(res3.messageMode, "wrap");
});

test("Default Preferences - Nested array immutability and frozen status", () => {
  assert.equal(Object.isFrozen(DEFAULT_COLUMN_PREFERENCES), true);
  assert.equal(Object.isFrozen(DEFAULT_COLUMN_PREFERENCES.visibleColumns), true);
  assert.equal(Object.isFrozen(DEFAULT_COLUMN_PREFERENCES.columnOrder), true);

  assert.throws(
    () => {
      // @ts-expect-error test runtime mutation prevention
      DEFAULT_COLUMN_PREFERENCES.visibleColumns.push("id");
    },
    { name: "TypeError" }
  );

  assert.throws(
    () => {
      // @ts-expect-error test runtime mutation prevention
      DEFAULT_COLUMN_PREFERENCES.columnOrder.push("id");
    },
    { name: "TypeError" }
  );
});

test("Persistence - SSR safety when window or localStorage is undefined", () => {
  // In Node environment, window is undefined by default
  assert.equal(typeof window, "undefined");
  assert.equal(loadStoredPreferences(), null);
  assert.equal(saveStoredPreferences(DEFAULT_COLUMN_PREFERENCES), false);
  const reset = resetStoredPreferences();
  assert.deepEqual(reset, {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: "truncate",
  });
});

test("Persistence - localStorage property getter throwing (Restricted Security Context)", () => {
  const originalWindow = globalThis.window;

  // Simulate restricted browser environment where window.localStorage getter throws SecurityError
  globalThis.window = {};
  Object.defineProperty(globalThis.window, "localStorage", {
    get() {
      throw new Error("SecurityError: Access to localStorage is denied in this context");
    },
    configurable: true,
  });

  try {
    // loadStoredPreferences() must catch SecurityError and return null
    assert.equal(loadStoredPreferences(), null);

    // saveStoredPreferences() must catch SecurityError and return false
    assert.equal(saveStoredPreferences(DEFAULT_COLUMN_PREFERENCES), false);

    // resetStoredPreferences() must catch SecurityError and return fresh defaults without throwing
    const reset = resetStoredPreferences();
    assert.deepEqual(reset, {
      visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
      columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
      messageMode: "truncate",
    });
  } finally {
    globalThis.window = originalWindow;
  }
});

test("Persistence - getItem(), setItem(), and removeItem() throwing exceptions", () => {
  const originalWindow = globalThis.window;

  // 1. Test getItem() throwing
  globalThis.window = {
    localStorage: {
      getItem: () => {
        throw new Error("DOMException: Corrupted read operation");
      },
      setItem: () => {},
      removeItem: () => {},
    },
  };

  try {
    assert.equal(loadStoredPreferences(), null);
  } finally {
    globalThis.window = originalWindow;
  }

  // 2. Test setItem() throwing (e.g. QuotaExceededError)
  globalThis.window = {
    localStorage: {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError: Storage quota has been exceeded");
      },
      removeItem: () => {},
    },
  };

  try {
    assert.equal(saveStoredPreferences(DEFAULT_COLUMN_PREFERENCES), false);
  } finally {
    globalThis.window = originalWindow;
  }

  // 3. Test removeItem() throwing
  globalThis.window = {
    localStorage: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {
        throw new Error("SecurityError: Unable to remove item");
      },
    },
  };

  try {
    const reset = resetStoredPreferences();
    assert.deepEqual(reset, {
      visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
      columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
      messageMode: "truncate",
    });
  } finally {
    globalThis.window = originalWindow;
  }
});

test("Persistence - Serialization and deserialization roundtrip with storage mock", () => {
  const mockStorage = new Map();
  const originalWindow = globalThis.window;

  // Setup minimal mock window
  globalThis.window = {
    localStorage: {
      getItem: (key) => mockStorage.get(key) || null,
      setItem: (key, val) => mockStorage.set(key, String(val)),
      removeItem: (key) => mockStorage.delete(key),
    },
  };

  try {
    const testPrefs = {
      visibleColumns: ["timestamp", "message", "trace_id", "environment"],
      columnOrder: [
        "timestamp",
        "message",
        "trace_id",
        "environment",
        "log_level",
        "service_name",
        "host",
        "span_id",
        "id",
      ],
      messageMode: "wrap",
    };

    // Save
    const saved = saveStoredPreferences(testPrefs);
    assert.equal(saved, true);
    assert.ok(mockStorage.has(STORAGE_KEY));

    // Load
    const loaded = loadStoredPreferences();
    assert.deepEqual(loaded, testPrefs);

    // Corrupt storage payload
    mockStorage.set(STORAGE_KEY, "{ corrupted_json: true, broken... ");
    const fallback = loadStoredPreferences();
    assert.equal(fallback, null);

    // Reset
    const afterReset = resetStoredPreferences();
    assert.equal(mockStorage.has(STORAGE_KEY), false);
    assert.deepEqual(afterReset, {
      visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
      columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
      messageMode: "truncate",
    });
  } finally {
    globalThis.window = originalWindow;
  }
});

