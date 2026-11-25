/**
 * Almacenes sobre Cloudflare D1.
 *
 * No importan nada de Cloudflare: usan la forma mínima de la API de D1
 * (`D1Like`), así que sirve el binding de un Worker tal cual.
 *
 * Las tablas se crean con `createSchema()` o copiando las sentencias de
 * `d1Schema()` a una migración.
 */
import {
  checkCap,
  checkLimit,
  paginate,
  StoreError,
  toMs,
  type AddResult,
  type FeedPage,
  type FeedQuery,
} from '../core/inbox.js';
import type { QuietHours } from '../core/quiet.js';
import type { Change, Draft, Overrides } from '../core/types.js';
import type { NotificationStore, PreferenceStore, QuietHoursStore } from '../ports.js';
import {
  COLUMNS,
  feedFilter,
  fromRow,
  insertValues,
  Params,
  readAllFilter,
  type Row,
  stored,
  visible,
} from './sql-common.js';

/** Lo que los almacenes usan de una sentencia preparada de D1. */
export interface D1StatementLike {
  bind(...values: unknown[]): D1StatementLike;
  all<T = Record<string, unknown>>(): Promise<{ results: T[] }>;
  run(): Promise<{ meta: { changes?: number } }>;
}

/** Lo que los almacenes usan de una base D1. El binding de un Worker la cumple. */
export interface D1Like {
  prepare(sql: string): D1StatementLike;
  /** Corre las sentencias en una transacción: todas o ninguna. */
  batch(statements: D1StatementLike[]): Promise<unknown>;
}

const TABLE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const NOTIFICATIONS = 'campanita_notifications';
const PREFERENCES = 'campanita_preferences';
const QUIET_HOURS = 'campanita_quiet_hours';
function checkTable(table: string): string {
  if (!TABLE.test(table)) throw new TypeError('el nombre de la tabla tiene que ser un identificador');
  return table;
}

interface SchemaByTable {
  notifications: string[];
  preferences: string[];
  quietHours: string[];
}

/** Las sentencias de cada tabla, por nombre: cada almacén crea sólo las suyas. */
function d1SchemaByTable(
  tables: {
    notifications?: string;
    preferences?: string;
    quietHours?: string;
  } = {},
): SchemaByTable {
  const n = checkTable(tables.notifications ?? NOTIFICATIONS);
  const p = checkTable(tables.preferences ?? PREFERENCES);
  const q = checkTable(tables.quietHours ?? QUIET_HOURS);
  return {
    notifications: [
      `CREATE TABLE IF NOT EXISTS ${n} (
  id            TEXT PRIMARY KEY,
  recipient_id  TEXT NOT NULL,
  type          TEXT NOT NULL,
  category      TEXT NOT NULL,
  title         TEXT NOT NULL,
  body          TEXT,
  action_url    TEXT,
  payload       TEXT NOT NULL,
  scope         TEXT,
  dedupe_key    TEXT,
  created_at_ms INTEGER NOT NULL,
  read_at_ms    INTEGER
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS ${n}_dedupe ON ${n} (recipient_id, dedupe_key) WHERE dedupe_key IS NOT NULL`,
      `CREATE INDEX IF NOT EXISTS ${n}_feed ON ${n} (recipient_id, created_at_ms DESC, id DESC)`,
    ],
    preferences: [
      // "Sin ámbito" y "ámbito de texto vacío" son cosas distintas, y un NULL no
      // entra en una clave primaria: `has_scope` los separa.
      `CREATE TABLE IF NOT EXISTS ${p} (
  recipient_id TEXT NOT NULL,
  has_scope    INTEGER NOT NULL,
  scope        TEXT NOT NULL,
  category     TEXT NOT NULL,
  channel      TEXT NOT NULL,
  enabled      INTEGER NOT NULL,
  PRIMARY KEY (recipient_id, has_scope, scope, category, channel)
) WITHOUT ROWID`,
    ],
    quietHours: [
      `CREATE TABLE IF NOT EXISTS ${q} (
  recipient_id TEXT PRIMARY KEY,
  enabled      INTEGER NOT NULL,
  start_time   TEXT NOT NULL,
  end_time     TEXT NOT NULL,
  timezone     TEXT NOT NULL
) WITHOUT ROWID`,
    ],
  };
}

/**
 * Las sentencias que crean las tablas y los índices, para copiar a una
 * migración. El texto se compara byte a byte (la colación por omisión de
 * SQLite), que en UTF-8 es el orden por punto de código que pide el spec.
 */
export function d1Schema(
  tables: {
    notifications?: string;
    preferences?: string;
    quietHours?: string;
  } = {},
): string[] {
  const byTable = d1SchemaByTable(tables);
  return [...byTable.notifications, ...byTable.preferences, ...byTable.quietHours];
}

/** Los avisos, sobre D1. */
export class D1Store implements NotificationStore {
  readonly #db: D1Like;
  readonly #table: string;

  constructor(db: D1Like, options: { table?: string } = {}) {
    this.#db = db;
    this.#table = checkTable(options.table ?? NOTIFICATIONS);
  }

  /** Crea la tabla y los índices si no existen. */
  async createSchema(): Promise<void> {
    const statements = d1SchemaByTable({ notifications: this.#table }).notifications;
    await this.#db.batch(statements.map((sql) => this.#db.prepare(sql)));
  }

  async add(draft: Draft, options: { id: string; createdAt: Date }): Promise<AddResult> {
    const createdAtMs = toMs(options.createdAt);
    // Una sola sentencia: si el identificador o la clave ya existen, no
    // inserta. Así dos pedidos a la vez con la misma clave dejan un solo aviso.
    const inserted = await this.#db
      .prepare(`INSERT INTO ${this.#table} (${COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) ON CONFLICT DO NOTHING`)
      .bind(...insertValues(draft, options.id, createdAtMs))
      .run();
    if ((inserted.meta.changes ?? 0) > 0) return { notification: stored(draft, options.id, createdAtMs), created: true };
    // No insertó. La clave repetida se revisa antes que el identificador.
    if (draft.dedupeKey !== null) {
      const existing = await this.#db
        .prepare(`SELECT ${COLUMNS} FROM ${this.#table} WHERE recipient_id = ? AND dedupe_key = ?`)
        .bind(draft.recipientId, draft.dedupeKey)
        .all<Row>();
      const row = existing.results[0];
      if (row !== undefined) return { notification: fromRow(row), created: false };
    }
    throw new StoreError('duplicate_id');
  }

  async listFeed(query: FeedQuery): Promise<FeedPage> {
    const limit = checkLimit(query.limit);
    const params = new Params(false);
    const filter = feedFilter(query, params);
    // Uno de más, para saber si queda otra página sin contar.
    const found = await this.#db
      .prepare(`SELECT ${COLUMNS} FROM ${this.#table} WHERE ${filter} ORDER BY created_at_ms DESC, id DESC LIMIT ?`)
      .bind(...params.values, limit + 1)
      .all<Row>();
    return paginate(found.results.map(fromRow), limit);
  }

  async countUnread(
    recipientId: string,
    options: { scope?: string | null; cap?: number | null } = {},
  ): Promise<number> {
    const cap = checkCap(options.cap);
    const params = new Params(false);
    let inner = `SELECT 1 FROM ${this.#table} WHERE ${visible(recipientId, options.scope ?? null, params)} AND read_at_ms IS NULL`;
    if (cap !== null) inner += ` LIMIT ${params.add(cap)}`;
    const counted = await this.#db
      .prepare(`SELECT COUNT(*) AS total FROM (${inner})`)
      .bind(...params.values)
      .all<{ total: number }>();
    return counted.results[0]?.total ?? 0;
  }

  async markRead(id: string, recipientId: string, readAt: Date): Promise<boolean> {
    const updated = await this.#db
      .prepare(`UPDATE ${this.#table} SET read_at_ms = ? WHERE id = ? AND recipient_id = ? AND read_at_ms IS NULL`)
      .bind(toMs(readAt), id, recipientId)
      .run();
    return (updated.meta.changes ?? 0) > 0;
  }

  async markAllRead(
    recipientId: string,
    options: { before: Date; readAt: Date; scope?: string | null; category?: string | null },
  ): Promise<number> {
    const params = new Params(false, [toMs(options.readAt)]);
    const filter = readAllFilter(recipientId, options, params);
    const updated = await this.#db
      .prepare(`UPDATE ${this.#table} SET read_at_ms = ? WHERE ${filter}`)
      .bind(...params.values)
      .run();
    return updated.meta.changes ?? 0;
  }
}

/** Las preferencias guardadas, sobre D1. */
export class D1PreferenceStore implements PreferenceStore {
  readonly #db: D1Like;
  readonly #table: string;

  constructor(db: D1Like, options: { table?: string } = {}) {
    this.#db = db;
    this.#table = checkTable(options.table ?? PREFERENCES);
  }

  /** Crea la tabla si no existe. */
  async createSchema(): Promise<void> {
    const statements = d1SchemaByTable({ preferences: this.#table }).preferences;
    await this.#db.batch(statements.map((sql) => this.#db.prepare(sql)));
  }

  async getOverrides(recipientId: string, options: { scope?: string | null } = {}): Promise<Overrides> {
    const scope = options.scope ?? null;
    const found = await this.#db
      .prepare(
        `SELECT category, channel, enabled FROM ${this.#table} WHERE recipient_id = ? AND has_scope = ? AND scope = ?`,
      )
      .bind(recipientId, scope === null ? 0 : 1, scope ?? '')
      .all<{ category: string; channel: string; enabled: number }>();
    // Sin prototipo, para que una categoría `__proto__` sea una clave más.
    const out: Record<string, Record<string, boolean>> = Object.create(null);
    for (const row of found.results) {
      let cells = out[row.category];
      if (cells === undefined) {
        cells = Object.create(null) as Record<string, boolean>;
        out[row.category] = cells;
      }
      cells[row.channel] = row.enabled !== 0;
    }
    return out;
  }

  async saveChanges(
    recipientId: string,
    changes: readonly Change[],
    options: { scope?: string | null } = {},
  ): Promise<void> {
    if (changes.length === 0) return;
    const scope = options.scope ?? null;
    const upsert = this.#db.prepare(
      `INSERT INTO ${this.#table} (recipient_id, has_scope, scope, category, channel, enabled) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (recipient_id, has_scope, scope, category, channel) DO UPDATE SET enabled = excluded.enabled`,
    );
    // `batch` es una transacción: se guardan todos los cambios o ninguno. Las
    // sentencias corren en orden, así que si una celda se repite vale la última.
    await this.#db.batch(
      changes.map((change) =>
        upsert.bind(recipientId, scope === null ? 0 : 1, scope ?? '', change.category, change.channel, change.enabled ? 1 : 0),
      ),
    );
  }
}

/** Los horarios de no molestar, sobre D1. */
export class D1QuietHoursStore implements QuietHoursStore {
  readonly #db: D1Like;
  readonly #table: string;

  constructor(db: D1Like, options: { table?: string } = {}) {
    this.#db = db;
    this.#table = checkTable(options.table ?? QUIET_HOURS);
  }

  /** Crea la tabla si no existe. */
  async createSchema(): Promise<void> {
    const statements = d1SchemaByTable({ quietHours: this.#table }).quietHours;
    await this.#db.batch(statements.map((sql) => this.#db.prepare(sql)));
  }

  async getQuietHours(recipientId: string): Promise<QuietHours | null> {
    const found = await this.#db
      .prepare(`SELECT enabled, start_time, end_time, timezone FROM ${this.#table} WHERE recipient_id = ?`)
      .bind(recipientId)
      .all<{
        enabled: number;
        start_time: string;
        end_time: string;
        timezone: string;
      }>();
    const row = found.results[0];
    if (row === undefined) return null;
    return {
      enabled: row.enabled !== 0,
      start: row.start_time,
      end: row.end_time,
      timezone: row.timezone,
    };
  }

  async setQuietHours(recipientId: string, quietHours: QuietHours): Promise<void> {
    await this.#db
      .prepare(
        `INSERT INTO ${this.#table} (recipient_id, enabled, start_time, end_time, timezone) VALUES (?, ?, ?, ?, ?)
ON CONFLICT (recipient_id) DO UPDATE SET enabled = excluded.enabled, start_time = excluded.start_time, end_time = excluded.end_time, timezone = excluded.timezone`,
      )
      .bind(recipientId, quietHours.enabled ? 1 : 0, quietHours.start, quietHours.end, quietHours.timezone)
      .run();
  }
}
