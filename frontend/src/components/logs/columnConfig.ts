/**
 * Canonical column definitions, configuration schema, validation, and
 * SSR-safe localStorage preference persistence for RootLens Log Explorer (Step 2C).
 */

export type ColumnId =
  | "timestamp"
  | "log_level"
  | "service_name"
  | "message"
  | "host"
  | "environment"
  | "trace_id"
  | "span_id"
  | "id";

export const ALL_COLUMN_IDS: readonly ColumnId[] = [
  "timestamp",
  "log_level",
  "service_name",
  "message",
  "host",
  "environment",
  "trace_id",
  "span_id",
  "id",
] as const;

export const ESSENTIAL_COLUMN_IDS: readonly ColumnId[] = [
  "timestamp",
  "message",
] as const;

export interface ColumnDefinition {
  id: ColumnId;
  label: string;
  description: string;
  minWidth: number;
  defaultVisible: boolean;
  isEssential: boolean;
}

export const AVAILABLE_COLUMNS: readonly ColumnDefinition[] = [
  {
    id: "timestamp",
    label: "Time (UTC)",
    description: "Event timestamp in UTC ISO-8601 format",
    minWidth: 140,
    defaultVisible: true,
    isEssential: true,
  },
  {
    id: "log_level",
    label: "Level",
    description: "Canonical severity level (e.g. ERROR, WARN, INFO, DEBUG)",
    minWidth: 80,
    defaultVisible: true,
    isEssential: false,
  },
  {
    id: "service_name",
    label: "Service",
    description: "Originating microservice or component identifier",
    minWidth: 130,
    defaultVisible: true,
    isEssential: false,
  },
  {
    id: "message",
    label: "Message",
    description: "Log message body or raw unparsed log record",
    minWidth: 260,
    defaultVisible: true,
    isEssential: true,
  },
  {
    id: "host",
    label: "Host / Source",
    description: "Host name, node identifier, or ingest format",
    minWidth: 120,
    defaultVisible: true,
    isEssential: false,
  },
  {
    id: "environment",
    label: "Environment",
    description: "Deployment environment (e.g. production, staging, development)",
    minWidth: 110,
    defaultVisible: false,
    isEssential: false,
  },
  {
    id: "trace_id",
    label: "Trace ID",
    description: "Distributed tracing correlation identifier",
    minWidth: 140,
    defaultVisible: false,
    isEssential: false,
  },
  {
    id: "span_id",
    label: "Span ID",
    description: "Distributed tracing span identifier",
    minWidth: 110,
    defaultVisible: false,
    isEssential: false,
  },
  {
    id: "id",
    label: "Doc ID",
    description: "Unique OpenSearch document identifier",
    minWidth: 140,
    defaultVisible: false,
    isEssential: false,
  },
] as const;

export type MessageDisplayMode = "truncate" | "wrap";

export interface UserColumnPreferences {
  visibleColumns: ColumnId[];
  columnOrder: ColumnId[];
  messageMode: MessageDisplayMode;
}

export interface DefaultColumnPreferences {
  readonly visibleColumns: readonly ColumnId[];
  readonly columnOrder: readonly ColumnId[];
  readonly messageMode: MessageDisplayMode;
}

export const DEFAULT_COLUMN_PREFERENCES: DefaultColumnPreferences = Object.freeze({
  visibleColumns: Object.freeze([
    "timestamp",
    "log_level",
    "service_name",
    "message",
    "host",
  ] as const),
  columnOrder: Object.freeze([
    "timestamp",
    "log_level",
    "service_name",
    "message",
    "host",
    "environment",
    "trace_id",
    "span_id",
    "id",
  ] as const),
  messageMode: "truncate",
});

export const STORAGE_KEY = "rootlens:log_table:preferences:v1";

const KNOWN_COLUMN_ID_SET = new Set<string>(ALL_COLUMN_IDS);

export function isColumnId(value: unknown): value is ColumnId {
  return typeof value === "string" && KNOWN_COLUMN_ID_SET.has(value);
}

/**
 * Validates arbitrary preference payloads, stripping unknown IDs, removing duplicates,
 * ensuring essential columns are never hidden, and reconciling complete column ordering.
 */
export function validatePreferences(raw: unknown): UserColumnPreferences {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {
      visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
      columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
      messageMode: DEFAULT_COLUMN_PREFERENCES.messageMode,
    };
  }

  const obj = raw as Record<string, unknown>;

  // 1. Validate & sanitize columnOrder first so visibleColumns can follow it
  const validOrder: ColumnId[] = [];
  if (Array.isArray(obj.columnOrder)) {
    const seen = new Set<ColumnId>();
    for (const item of obj.columnOrder) {
      if (isColumnId(item) && !seen.has(item)) {
        seen.add(item);
        validOrder.push(item);
      }
    }
  }

  // Reconcile: append any known column IDs that were omitted
  for (const id of ALL_COLUMN_IDS) {
    if (!validOrder.includes(id)) {
      validOrder.push(id);
    }
  }

  // 2. Validate & sanitize visibleColumns
  let validVisible: ColumnId[] = [];
  if (Array.isArray(obj.visibleColumns)) {
    const seen = new Set<ColumnId>();
    for (const item of obj.visibleColumns) {
      if (isColumnId(item) && !seen.has(item)) {
        seen.add(item);
        validVisible.push(item);
      }
    }
  } else {
    validVisible = [...DEFAULT_COLUMN_PREFERENCES.visibleColumns];
  }

  // Essential columns safeguard: timestamp and message must always be present
  for (const essential of ESSENTIAL_COLUMN_IDS) {
    if (!validVisible.includes(essential)) {
      validVisible.push(essential);
    }
  }

  // Sort visibleColumns according to the validated columnOrder
  validVisible.sort((a, b) => validOrder.indexOf(a) - validOrder.indexOf(b));

  // 3. Validate messageMode
  const messageMode: MessageDisplayMode =
    obj.messageMode === "wrap" ? "wrap" : "truncate";

  return {
    visibleColumns: validVisible,
    columnOrder: validOrder,
    messageMode,
  };
}

/**
 * Pure state helper: Toggles column visibility while guaranteeing essential columns cannot be hidden.
 */
export function toggleColumnVisibility(
  currentPrefs: UserColumnPreferences,
  columnId: ColumnId
): UserColumnPreferences {
  if (ESSENTIAL_COLUMN_IDS.includes(columnId)) {
    return currentPrefs;
  }

  const isVisible = currentPrefs.visibleColumns.includes(columnId);
  const newVisible = isVisible
    ? currentPrefs.visibleColumns.filter((id) => id !== columnId)
    : [...currentPrefs.visibleColumns, columnId];

  // Keep visible columns ordered according to columnOrder
  newVisible.sort(
    (a, b) =>
      currentPrefs.columnOrder.indexOf(a) - currentPrefs.columnOrder.indexOf(b)
  );

  const updated: UserColumnPreferences = {
    ...currentPrefs,
    visibleColumns: newVisible,
  };

  return validatePreferences(updated);
}

/**
 * Pure state helper: Swaps column position in columnOrder up or down within bounds.
 */
export function reorderColumn(
  currentPrefs: UserColumnPreferences,
  columnId: ColumnId,
  direction: "up" | "down"
): UserColumnPreferences {
  const order = [...currentPrefs.columnOrder];
  const currentIndex = order.indexOf(columnId);

  if (currentIndex === -1) {
    return currentPrefs;
  }

  const targetIndex = direction === "up" ? currentIndex - 1 : currentIndex + 1;
  if (targetIndex < 0 || targetIndex >= order.length) {
    return currentPrefs;
  }

  // Swap positions
  const temp = order[currentIndex];
  order[currentIndex] = order[targetIndex];
  order[targetIndex] = temp;

  // Re-sort visibleColumns to match the updated order
  const newVisible = [...currentPrefs.visibleColumns].sort(
    (a, b) => order.indexOf(a) - order.indexOf(b)
  );

  const updated: UserColumnPreferences = {
    ...currentPrefs,
    columnOrder: order,
    visibleColumns: newVisible,
  };

  return validatePreferences(updated);
}

/**
 * Pure helper: Filters column definitions by label, description, or ID.
 */
export function filterColumnDefinitions(
  columns: readonly ColumnDefinition[],
  query: string
): ColumnDefinition[] {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) {
    return [...columns];
  }
  return columns.filter(
    (col) =>
      col.label.toLowerCase().includes(trimmed) ||
      col.description.toLowerCase().includes(trimmed) ||
      col.id.toLowerCase().includes(trimmed)
  );
}

/**
 * Pure helper: Resolves the active list of column definitions ordered according to columnOrder
 * and filtered by visibleColumns.
 */
export function getVisibleColumns(
  preferences: UserColumnPreferences
): ColumnDefinition[] {
  const map = new Map<ColumnId, ColumnDefinition>(
    AVAILABLE_COLUMNS.map((col) => [col.id, col])
  );
  return preferences.columnOrder
    .filter((id) => preferences.visibleColumns.includes(id))
    .map((id) => map.get(id))
    .filter((col): col is ColumnDefinition => Boolean(col));
}

/**
 * Safely reads and validates column preferences from localStorage.
 * SSR-safe: returns null if executed in a server environment or if storage is inaccessible.
 */
export function loadStoredPreferences(): UserColumnPreferences | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const storage = window.localStorage;
    if (!storage) {
      return null;
    }
    const item = storage.getItem(STORAGE_KEY);
    if (!item) {
      return null;
    }
    const parsed = JSON.parse(item);
    return validatePreferences(parsed);
  } catch {
    return null;
  }
}

/**
 * Safely validates and writes column preferences to localStorage.
 * SSR-safe: returns false if executed in a server environment or if storage fails.
 */
export function saveStoredPreferences(
  preferences: UserColumnPreferences | DefaultColumnPreferences
): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    const storage = window.localStorage;
    if (!storage) {
      return false;
    }
    const validated = validatePreferences(preferences);
    storage.setItem(STORAGE_KEY, JSON.stringify(validated));
    return true;
  } catch {
    return false;
  }
}

/**
 * Resets preferences in localStorage and returns fresh default preferences.
 */
export function resetStoredPreferences(): UserColumnPreferences {
  if (typeof window !== "undefined") {
    try {
      const storage = window.localStorage;
      if (storage) {
        storage.removeItem(STORAGE_KEY);
      }
    } catch {
      // Ignore storage errors safely
    }
  }

  return {
    visibleColumns: [...DEFAULT_COLUMN_PREFERENCES.visibleColumns],
    columnOrder: [...DEFAULT_COLUMN_PREFERENCES.columnOrder],
    messageMode: DEFAULT_COLUMN_PREFERENCES.messageMode,
  };
}
