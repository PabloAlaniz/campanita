/**
 * Lo que comparten los almacenes SQL (D1 y Postgres): columnas, filas y
 * filtros de la bandeja. Cada base pone su marcador de parámetro (`?` o `$n`) y
 * su forma de ejecutar; el resto del SQL es el mismo.
 */
import { toMs, type FeedQuery, type Notification } from '../core/inbox.js';
import type { Draft } from '../core/types.js';

export const COLUMNS =
  'id, recipient_id, type, category, title, body, action_url, payload, scope, dedupe_key, created_at_ms, read_at_ms';

/** Una fila con las columnas de `COLUMNS`. */
export interface Row {
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
  // para devolver `bigint`; D1, un número. `Number` sirve para todos.
  created_at_ms: string | number | bigint;
  read_at_ms: string | number | bigint | null;
}

export function fromRow(row: Row): Notification {
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

/** Los valores de `COLUMNS` para un aviso nuevo, sin `read_at_ms`. */
export function insertValues(draft: Draft, id: string, createdAtMs: number): unknown[] {
  return [
    id,
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
  ];
}

/** El aviso que quedó guardado: una copia, no el objeto de quien llama. */
export function stored(draft: Draft, id: string, createdAtMs: number): Notification {
  return {
    ...draft,
    payload: JSON.parse(JSON.stringify(draft.payload)) as Record<string, unknown>,
    id,
    createdAt: new Date(createdAtMs),
    readAt: null,
  };
}

/** Los parámetros de una sentencia, con el marcador de cada base. */
export class Params {
  readonly values: unknown[];
  readonly #numbered: boolean;

  /** `numbered`: `$1`, `$2`… (Postgres). Si no, `?` (D1). */
  constructor(numbered: boolean, values: unknown[] = []) {
    this.#numbered = numbered;
    this.values = [...values];
  }

  /** Agrega un valor y devuelve su marcador. */
  add(value: unknown): string {
    this.values.push(value);
    return this.#numbered ? `$${this.values.length}` : '?';
  }
}

/** Lo que ve una persona: sin ámbito, todo; con ámbito, lo de ese ámbito y lo que no tiene. */
export function visible(recipientId: string, scope: string | null, params: Params): string {
  const recipient = `recipient_id = ${params.add(recipientId)}`;
  return scope === null ? recipient : `${recipient} AND (scope IS NULL OR scope = ${params.add(scope)})`;
}

/** El `WHERE` de una página de la bandeja, sin el orden ni el límite. */
export function feedFilter(query: FeedQuery, params: Params): string {
  let where = visible(query.recipientId, query.scope ?? null, params);
  if (query.unreadOnly) where += ' AND read_at_ms IS NULL';
  if ((query.category ?? null) !== null) where += ` AND category = ${params.add(query.category)}`;
  const cursor = query.cursor ?? null;
  if (cursor !== null) {
    where += ` AND (created_at_ms, id) < (${params.add(toMs(cursor.createdAt))}, ${params.add(cursor.id)})`;
  }
  return where;
}

/** El `WHERE` de `markAllRead`: lo visible, sin leer y anterior a `before`. */
export function readAllFilter(
  recipientId: string,
  options: { before: Date; scope?: string | null; category?: string | null },
  params: Params,
): string {
  let where = visible(recipientId, options.scope ?? null, params);
  where += ` AND read_at_ms IS NULL AND created_at_ms < ${params.add(toMs(options.before))}`;
  if ((options.category ?? null) !== null) where += ` AND category = ${params.add(options.category)}`;
  return where;
}
