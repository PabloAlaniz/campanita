/**
 * Almacenes sobre Postgres.
 *
 * No importan ningún driver: usan la forma mínima `PostgresLike`, que el
 * `Pool` y el `Client` de `pg` y el `Pool` de `@neondatabase/serverless`
 * cumplen tal cual. Para postgres.js está `fromPostgresJs`.
 *
 * Cada operación es una sola sentencia, así que no hace falta reservar una
 * conexión para una transacción: sirve un pool o un driver por HTTP.
 *
 * Las tablas se crean con `createSchema()` o copiando las sentencias de
 * `postgresSchema()` a una migración.
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
import type { QuietHours } from '../core/quiet.js';
import type { Change, Draft, Overrides } from '../core/types.js';
import type { NotificationStore, PreferenceStore, QuietHoursStore } from '../ports.js';

/**
 * Lo que los almacenes usan de una conexión a Postgres. El `Pool` y el `Client`
 * de `pg`, el `Pool` y el `Client` de `@neondatabase/serverless` y su driver
 * por HTTP con `neon(url, { fullResults: true })` la cumplen.
 *
 * Las filas tienen que venir con el nombre de la columna tal cual: sin pasarlo
 * a camelCase ni a modo arreglo.
 */
export interface PostgresLike {
  query(text: string, params?: unknown[]): Promise<{ rows: unknown[]; rowCount: number | null }>;
}

/** Lo que `fromPostgresJs` usa de una instancia de postgres.js. */
export interface PostgresJsLike {
  unsafe(text: string, params?: unknown[]): PromiseLike<ArrayLike<unknown> & { count: number | null }>;
}

/**
 * Adapta una instancia de postgres.js (`postgres(url)`) a `PostgresLike`. La
 * instancia no tiene que transformar los nombres de las columnas
 * (`transform: postgres.camel` y parecidos).
 */
export function fromPostgresJs(sql: PostgresJsLike): PostgresLike {
  return {
    async query(text, params = []) {
      const result = await sql.unsafe(text, params);
      return { rows: Array.from(result), rowCount: result.count };
    },
  };
}

// Postgres corta en silencio los nombres de más de 63 bytes. Los índices se
// llaman como la tabla más `_dedupe`, así que la tabla tiene hasta 56: con un
// nombre más largo, el índice cortado se llamaría como la tabla, el
// `IF NOT EXISTS` lo saltearía y la clave repetida dejaría de cuidarse.
const TABLE = /^[A-Za-z_][A-Za-z0-9_]{0,55}$/;
const NOTIFICATIONS = 'campanita_notifications';
const PREFERENCES = 'campanita_preferences';
const QUIET_HOURS = 'campanita_quiet_hours';
const COLUMNS =
  'id, recipient_id, type, category, title, body, action_url, payload, scope, dedupe_key, created_at_ms, read_at_ms';

function checkTable(table: string): string {
  if (!TABLE.test(table)) {
    throw new TypeError('el nombre de la tabla tiene que ser un identificador de hasta 56 caracteres');
  }
  return table;
}

/**
 * Cuántas filas cambió una sentencia. Un driver que no lo informe no sirve: con
 * cero, un aviso guardado se tomaría por repetido.
 */
function changed(result: { rowCount: number | null }): number {
  if (result.rowCount === null) throw new TypeError('el driver no informó cuántas filas cambió la sentencia');
  return result.rowCount;
}

interface SchemaByTable {
  notifications: string[];
  preferences: string[];
  quietHours: string[];
}

/** Las sentencias de cada tabla, por nombre: cada almacén crea sólo las suyas. */
function postgresSchemaByTable(
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
  id            TEXT COLLATE "C" PRIMARY KEY,
  recipient_id  TEXT NOT NULL,
  type          TEXT NOT NULL,
  category      TEXT NOT NULL,
  title         TEXT NOT NULL,
  body          TEXT,
  action_url    TEXT,
  payload       TEXT NOT NULL,
  scope         TEXT,
  dedupe_key    TEXT,
  created_at_ms BIGINT NOT NULL,
  read_at_ms    BIGINT
)`,
      `CREATE UNIQUE INDEX IF NOT EXISTS ${n}_dedupe ON ${n} (recipient_id, dedupe_key) WHERE dedupe_key IS NOT NULL`,
      `CREATE INDEX IF NOT EXISTS ${n}_feed ON ${n} (recipient_id, created_at_ms DESC, id DESC)`,
    ],
    preferences: [
      // "Sin ámbito" y "ámbito de texto vacío" son cosas distintas, y un NULL no
      // entra en una clave primaria: `has_scope` los separa.
      `CREATE TABLE IF NOT EXISTS ${p} (
  recipient_id TEXT NOT NULL,
  has_scope    BOOLEAN NOT NULL,
  scope        TEXT NOT NULL,
  category     TEXT NOT NULL,
  channel      TEXT NOT NULL,
  enabled      BOOLEAN NOT NULL,
  PRIMARY KEY (recipient_id, has_scope, scope, category, channel)
)`,
    ],
    quietHours: [
      `CREATE TABLE IF NOT EXISTS ${q} (
  recipient_id TEXT PRIMARY KEY,
  enabled      BOOLEAN NOT NULL,
  start_time   TEXT NOT NULL,
  end_time     TEXT NOT NULL,
  timezone     TEXT NOT NULL
)`,
    ],
  };
}

/**
 * Las sentencias que crean las tablas y los índices, para copiar a una
 * migración.
 *
 * El `id` usa la colación "C", que compara bytes: en UTF-8 es el orden por
 * punto de código que pide el spec. Con la colación de la base (en_US y
 * parecidas) el orden de la bandeja sale distinto.
 *
 * Postgres no guarda el carácter U+0000 en un TEXT: un aviso que lo tenga en el
 * título, el cuerpo o algún identificador se rechaza con el error del driver.
 * En el payload no pasa, porque JSON lo escapa.
 */
export function postgresSchema(
  tables: {
    notifications?: string;
    preferences?: string;
    quietHours?: string;
  } = {},
): string[] {
  const byTable = postgresSchemaByTable(tables);
  return [...byTable.notifications, ...byTable.preferences, ...byTable.quietHours];
}

async function runAll(db: PostgresLike, statements: string[]): Promise<void> {
  for (const sql of statements) await db.query(sql);
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
  // `pg` y postgres.js devuelven BIGINT como texto, salvo que se configuren
  // para devolver `bigint`. `Number` sirve para los tres.
  created_at_ms: string | number | bigint;
  read_at_ms: string | number | bigint | null;
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
    createdAt: new Date(Number(row.created_at_ms)),
    readAt: row.read_at_ms === null ? null : new Date(Number(row.read_at_ms)),
  };
}

/** `recipient_id = $n` y, con ámbito, también el filtro de ámbito del spec. */
function visible(recipientId: string, scope: string | null, params: unknown[]): string {
  params.push(recipientId);
  const recipient = `recipient_id = $${params.length}`;
  if (scope === null) return recipient;
  params.push(scope);
  return `${recipient} AND (scope IS NULL OR scope = $${params.length})`;
}

/** Agrega un parámetro y devuelve su marcador. */
function param(params: unknown[], value: unknown): string {
  params.push(value);
  return `$${params.length}`;
}

/** Los avisos, sobre Postgres. */
export class PostgresStore implements NotificationStore {
  readonly #db: PostgresLike;
  readonly #table: string;

  constructor(db: PostgresLike, options: { table?: string } = {}) {
    this.#db = db;
    this.#table = checkTable(options.table ?? NOTIFICATIONS);
  }

  /** Crea la tabla y los índices si no existen. */
  async createSchema(): Promise<void> {
    await runAll(this.#db, postgresSchemaByTable({ notifications: this.#table }).notifications);
  }

  async add(draft: Draft, options: { id: string; createdAt: Date }): Promise<AddResult> {
    const createdAtMs = toMs(options.createdAt);
    // Una sola sentencia: si el identificador o la clave ya existen, no
    // inserta. Si otro pedido está guardando la misma clave, Postgres espera a
    // que termine, así que la búsqueda de abajo encuentra su aviso.
    const inserted = await this.#db.query(
      `INSERT INTO ${this.#table} (${COLUMNS}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NULL) ON CONFLICT DO NOTHING`,
      [
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
      ],
    );
    if (changed(inserted) > 0) {
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
      const existing = await this.#db.query(
        `SELECT ${COLUMNS} FROM ${this.#table} WHERE recipient_id = $1 AND dedupe_key = $2`,
        [draft.recipientId, draft.dedupeKey],
      );
      const row = existing.rows[0] as Row | undefined;
      if (row !== undefined) return { notification: fromRow(row), created: false };
    }
    throw new StoreError('duplicate_id');
  }

  async listFeed(query: FeedQuery): Promise<FeedPage> {
    const limit = checkLimit(query.limit);
    const params: unknown[] = [];
    let filter = visible(query.recipientId, query.scope ?? null, params);
    if (query.unreadOnly) filter += ' AND read_at_ms IS NULL';
    if ((query.category ?? null) !== null) filter += ` AND category = ${param(params, query.category)}`;
    const cursor = query.cursor ?? null;
    if (cursor !== null) {
      filter += ` AND (created_at_ms, id) < (${param(params, toMs(cursor.createdAt))}, ${param(params, cursor.id)})`;
    }
    // Uno de más, para saber si queda otra página sin contar.
    const found = await this.#db.query(
      `SELECT ${COLUMNS} FROM ${this.#table} WHERE ${filter} ORDER BY created_at_ms DESC, id DESC LIMIT ${param(params, limit + 1)}`,
      params,
    );
    const rows = found.rows as Row[];
    const items = rows.slice(0, limit).map(fromRow);
    const last = items.at(-1);
    return {
      items,
      nextCursor:
        last !== undefined && rows.length > limit ? { createdAt: new Date(last.createdAt), id: last.id } : null,
    };
  }

  async countUnread(
    recipientId: string,
    options: { scope?: string | null; cap?: number | null } = {},
  ): Promise<number> {
    const cap = checkCap(options.cap);
    const params: unknown[] = [];
    let inner = `SELECT 1 FROM ${this.#table} WHERE ${visible(recipientId, options.scope ?? null, params)} AND read_at_ms IS NULL`;
    if (cap !== null) inner += ` LIMIT ${param(params, cap)}`;
    const counted = await this.#db.query(`SELECT COUNT(*) AS total FROM (${inner}) AS unread`, params);
    const row = counted.rows[0] as { total: string | number | bigint } | undefined;
    return Number(row?.total ?? 0);
  }

  async markRead(id: string, recipientId: string, readAt: Date): Promise<boolean> {
    const updated = await this.#db.query(
      `UPDATE ${this.#table} SET read_at_ms = $1 WHERE id = $2 AND recipient_id = $3 AND read_at_ms IS NULL`,
      [toMs(readAt), id, recipientId],
    );
    return changed(updated) > 0;
  }

  async markAllRead(
    recipientId: string,
    options: { before: Date; readAt: Date; scope?: string | null; category?: string | null },
  ): Promise<number> {
    const params: unknown[] = [toMs(options.readAt)];
    let filter = visible(recipientId, options.scope ?? null, params);
    filter += ` AND read_at_ms IS NULL AND created_at_ms < ${param(params, toMs(options.before))}`;
    if ((options.category ?? null) !== null) filter += ` AND category = ${param(params, options.category)}`;
    const updated = await this.#db.query(`UPDATE ${this.#table} SET read_at_ms = $1 WHERE ${filter}`, params);
    return changed(updated);
  }
}

/** Las preferencias guardadas, sobre Postgres. */
export class PostgresPreferenceStore implements PreferenceStore {
  readonly #db: PostgresLike;
  readonly #table: string;

  constructor(db: PostgresLike, options: { table?: string } = {}) {
    this.#db = db;
    this.#table = checkTable(options.table ?? PREFERENCES);
  }

  /** Crea la tabla si no existe. */
  async createSchema(): Promise<void> {
    await runAll(this.#db, postgresSchemaByTable({ preferences: this.#table }).preferences);
  }

  async getOverrides(recipientId: string, options: { scope?: string | null } = {}): Promise<Overrides> {
    const scope = options.scope ?? null;
    const found = await this.#db.query(
      `SELECT category, channel, enabled FROM ${this.#table} WHERE recipient_id = $1 AND has_scope = $2 AND scope = $3`,
      [recipientId, scope !== null, scope ?? ''],
    );
    // Sin prototipo, para que una categoría `__proto__` sea una clave más.
    const out: Record<string, Record<string, boolean>> = Object.create(null);
    for (const row of found.rows as { category: string; channel: string; enabled: boolean }[]) {
      let cells = out[row.category];
      if (cells === undefined) {
        cells = Object.create(null) as Record<string, boolean>;
        out[row.category] = cells;
      }
      cells[row.channel] = row.enabled;
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
    // Si una celda se repite vale la última. Postgres no deja que una misma
    // sentencia actualice dos veces la misma fila, así que se resuelve acá.
    const cells = new Map<string, Change>();
    for (const change of changes) {
      const key = JSON.stringify([change.category, change.channel]);
      cells.delete(key);
      cells.set(key, change);
    }
    const last = [...cells.values()];
    // Una sola sentencia: se guardan todos los cambios o ninguno. El orden fijo
    // hace que dos pedidos a la vez bloqueen las filas en el mismo orden y no
    // se traben entre sí.
    await this.#db.query(
      `INSERT INTO ${this.#table} (recipient_id, has_scope, scope, category, channel, enabled)
SELECT $1, $2, $3, category, channel, enabled FROM unnest($4::text[], $5::text[], $6::boolean[]) AS cell (category, channel, enabled)
ORDER BY category, channel
ON CONFLICT (recipient_id, has_scope, scope, category, channel) DO UPDATE SET enabled = excluded.enabled`,
      [
        recipientId,
        scope !== null,
        scope ?? '',
        last.map((change) => change.category),
        last.map((change) => change.channel),
        last.map((change) => change.enabled),
      ],
    );
  }
}

/** Los horarios de no molestar, sobre Postgres. */
export class PostgresQuietHoursStore implements QuietHoursStore {
  readonly #db: PostgresLike;
  readonly #table: string;

  constructor(db: PostgresLike, options: { table?: string } = {}) {
    this.#db = db;
    this.#table = checkTable(options.table ?? QUIET_HOURS);
  }

  /** Crea la tabla si no existe. */
  async createSchema(): Promise<void> {
    await runAll(this.#db, postgresSchemaByTable({ quietHours: this.#table }).quietHours);
  }

  async getQuietHours(recipientId: string): Promise<QuietHours | null> {
    const found = await this.#db.query(
      `SELECT enabled, start_time, end_time, timezone FROM ${this.#table} WHERE recipient_id = $1`,
      [recipientId],
    );
    const row = found.rows[0] as
      | { enabled: boolean; start_time: string; end_time: string; timezone: string }
      | undefined;
    if (row === undefined) return null;
    return { enabled: row.enabled, start: row.start_time, end: row.end_time, timezone: row.timezone };
  }

  async setQuietHours(recipientId: string, quietHours: QuietHours): Promise<void> {
    await this.#db.query(
      `INSERT INTO ${this.#table} (recipient_id, enabled, start_time, end_time, timezone) VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (recipient_id) DO UPDATE SET enabled = excluded.enabled, start_time = excluded.start_time, end_time = excluded.end_time, timezone = excluded.timezone`,
      [recipientId, quietHours.enabled, quietHours.start, quietHours.end, quietHours.timezone],
    );
  }
}
