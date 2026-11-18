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
  StoreError,
  toMs,
  type AddResult,
  type FeedPage,
  type FeedQuery,
  type Notification,
} from '../core/inbox.js';
import type { Change, Draft, Overrides } from '../core/types.js';
import type { NotificationStore, PreferenceStore } from '../ports.js';

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
const COLUMNS =
  'id, recipient_id, type, category, title, body, action_url, payload, scope, dedupe_key, created_at_ms, read_at_ms';

function checkTable(table: string): string {
  if (!TABLE.test(table)) throw new TypeError('el nombre de la tabla tiene que ser un identificador');
  return table;
}

/**
 * Las sentencias que crean las tablas y los índices, para copiar a una
 * migración. El texto se compara byte a byte (la colación por omisión de
 * SQLite), que en UTF-8 es el orden por punto de código que pide el spec.
 */
export function d1Schema(tables: { notifications?: string; preferences?: string } = {}): string[] {
  const n = checkTable(tables.notifications ?? NOTIFICATIONS);
  const p = checkTable(tables.preferences ?? PREFERENCES);
  return [
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
  ];
}

interface Row {
  id: string;
  recipient_id: string;
  type: string;
  category: string;
  title: string;
  body: string | null;
  action_url: string | null;
  payload: string;
  scope: string | null;
  dedupe_key: string | null;
  created_at_ms: number;
  read_at_ms: number | null;
}

function fromRow(row: Row): Notification {
  return {
    id: row.id,
    recipientId: row.recipient_id,
    type: row.type,
    category: row.category,
    title: row.title,
    body: row.body,
    actionUrl: row.action_url,
    payload: JSON.parse(row.payload) as Record<string, unknown>,
    scope: row.scope,
    dedupeKey: row.dedupe_key,
    createdAt: new Date(row.created_at_ms),
    readAt: row.read_at_ms === null ? null : new Date(row.read_at_ms),
  };
}

/** `recipient_id = ?` y, con ámbito, también el filtro de ámbito del spec. */
function visible(recipientId: string, scope: string | null): { where: string; params: unknown[] } {
  return scope === null
    ? { where: 'recipient_id = ?', params: [recipientId] }
    : { where: 'recipient_id = ? AND (scope IS NULL OR scope = ?)', params: [recipientId, scope] };
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
    const statements = d1Schema({ notifications: this.#table }).slice(0, 3);
    await this.#db.batch(statements.map((sql) => this.#db.prepare(sql)));
  }

  async add(draft: Draft, options: { id: string; createdAt: Date }): Promise<AddResult> {
    const createdAtMs = toMs(options.createdAt);
    // Una sola sentencia: si el identificador o la clave ya existen, no
    // inserta. Así dos pedidos a la vez con la misma clave dejan un solo aviso.
    const inserted = await this.#db
      .prepare(`INSERT INTO ${this.#table} (${COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) ON CONFLICT DO NOTHING`)
      .bind(
        options.id,
        draft.recipientId,
        draft.type,
        draft.category,
        draft.title,
        draft.body,
        draft.actionUrl,
        JSON.stringify(draft.payload),
        draft.scope,
        draft.dedupeKey,
        createdAtMs,
      )
      .run();
    if ((inserted.meta.changes ?? 0) > 0) {
      const notification: Notification = {
        ...draft,
        // Lo que se devuelve es lo que quedó guardado, no el objeto de quien llama.
        payload: JSON.parse(JSON.stringify(draft.payload)) as Record<string, unknown>,
        id: options.id,
        createdAt: new Date(createdAtMs),
        readAt: null,
      };
      return { notification, created: true };
    }
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
    const { where, params } = visible(query.recipientId, query.scope ?? null);
    let filter = where;
    if (query.unreadOnly) filter += ' AND read_at_ms IS NULL';
    if ((query.category ?? null) !== null) {
      filter += ' AND category = ?';
      params.push(query.category);
    }
    const cursor = query.cursor ?? null;
    if (cursor !== null) {
      filter += ' AND (created_at_ms, id) < (?, ?)';
      params.push(toMs(cursor.createdAt), cursor.id);
    }
    // Uno de más, para saber si queda otra página sin contar.
    const found = await this.#db
      .prepare(`SELECT ${COLUMNS} FROM ${this.#table} WHERE ${filter} ORDER BY created_at_ms DESC, id DESC LIMIT ?`)
      .bind(...params, limit + 1)
      .all<Row>();
    const items = found.results.slice(0, limit).map(fromRow);
    const last = items.at(-1);
    return {
      items,
      nextCursor:
        last !== undefined && found.results.length > limit
          ? { createdAt: new Date(last.createdAt), id: last.id }
          : null,
    };
  }

  async countUnread(
    recipientId: string,
    options: { scope?: string | null; cap?: number | null } = {},
  ): Promise<number> {
    const cap = checkCap(options.cap);
    const { where, params } = visible(recipientId, options.scope ?? null);
    let inner = `SELECT 1 FROM ${this.#table} WHERE ${where} AND read_at_ms IS NULL`;
    if (cap !== null) {
      inner += ' LIMIT ?';
      params.push(cap);
    }
    const counted = await this.#db
      .prepare(`SELECT COUNT(*) AS total FROM (${inner})`)
      .bind(...params)
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
    const { where, params } = visible(recipientId, options.scope ?? null);
    let filter = `${where} AND read_at_ms IS NULL AND created_at_ms < ?`;
    params.push(toMs(options.before));
    if ((options.category ?? null) !== null) {
      filter += ' AND category = ?';
      params.push(options.category);
    }
    const updated = await this.#db
      .prepare(`UPDATE ${this.#table} SET read_at_ms = ? WHERE ${filter}`)
      .bind(toMs(options.readAt), ...params)
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
    const sql = d1Schema({ preferences: this.#table })[3]!;
    await this.#db.batch([this.#db.prepare(sql)]);
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
      const cells = (out[row.category] ??= Object.create(null) as Record<string, boolean>);
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
