/**
 * La forma de la bandeja en el cable: lo que viaja entre el adaptador HTTP y la
 * interfaz. Está en la sección "La bandeja por HTTP" del spec.
 */
import type { Notification } from '../core/inbox.js';
import type { QuietHours } from '../core/quiet.js';
import type { Matrix } from '../core/types.js';

/** Un aviso como lo devuelve `GET /feed`. Las fechas son ISO 8601 en UTC con milisegundos. */
export interface InboxItem {
  readonly id: string;
  readonly type: string;
  readonly category: string;
  readonly title: string;
  readonly body: string | null;
  readonly action_url: string | null;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly scope: string | null;
  readonly created_at: string;
  readonly read_at: string | null;
}

export interface FeedResponse {
  readonly items: readonly InboxItem[];
  /** `null` cuando no queda nada más. Es opaco: se devuelve tal cual en `cursor`. */
  readonly next_cursor: string | null;
}

export interface UnreadCountResponse {
  readonly count: number;
  /** Hay más que `count`: la interfaz muestra `99+`. */
  readonly capped: boolean;
}

export interface ChangedResponse<T extends boolean | number> {
  readonly changed: T;
}

export interface PreferencesResponse {
  readonly categories: Matrix;
}

export interface QuietHoursResponse {
  readonly quiet_hours: QuietHours | null;
}

/** Los códigos de error del contrato. Los de `validatePatch` llegan con `category` y `channel`. */
export type InboxErrorCode =
  | 'not_found'
  | 'method_not_allowed'
  | 'unauthenticated'
  | 'invalid_limit'
  | 'invalid_cursor'
  | 'invalid_cap'
  | 'invalid_query'
  | 'invalid_body'
  | 'unknown_category'
  | 'unknown_channel'
  | 'invalid_value'
  | 'locked'
  | 'unavailable'
  | 'invalid_quiet_hours'
  | 'unknown_timezone';

export interface InboxErrorBody {
  readonly error: {
    readonly code: InboxErrorCode;
    readonly message: string;
    readonly category?: string;
    readonly channel?: string | null;
  };
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

/** `AAAA-MM-DDTHH:MM:SS.mmmZ`. */
export function toIso(moment: Date): string {
  return moment.toISOString();
}

/**
 * El inverso de `toIso`, estricto: sólo acepta texto que, escrito de nuevo, da
 * lo mismo (un 30 de febrero no), y fechas desde 1970.
 */
export function fromIso(text: unknown): Date | null {
  if (typeof text !== 'string' || !ISO.test(text) || text < '1970') return null;
  const ms = Date.parse(text);
  if (Number.isNaN(ms)) return null;
  const moment = new Date(ms);
  return moment.toISOString() === text ? moment : null;
}

export function itemToWire(n: Notification): InboxItem {
  return {
    id: n.id,
    type: n.type,
    category: n.category,
    title: n.title,
    body: n.body,
    action_url: n.actionUrl,
    payload: n.payload,
    scope: n.scope,
    created_at: toIso(n.createdAt),
    read_at: n.readAt === null ? null : toIso(n.readAt),
  };
}

// ── El cursor ───────────────────────────────────────────────────────

const B64URL = /^[A-Za-z0-9_-]*$/;
const DECIMAL = /^(0|[1-9][0-9]*)$/;

function base64url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** El base64url sin relleno de `<created_at_ms>:<id>`. */
export function encodeCursor(cursor: { readonly createdAt: Date; readonly id: string }): string {
  return base64url(new TextEncoder().encode(`${cursor.createdAt.getTime()}:${cursor.id}`));
}

/** `null` si el texto no es exactamente un cursor que devolvería `encodeCursor`. */
export function decodeCursor(text: string): { createdAt: Date; id: string } | null {
  if (!B64URL.test(text) || text.length % 4 === 1) return null;
  let raw: string;
  try {
    const binary = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    raw = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
  const colon = raw.indexOf(':');
  if (colon < 0) return null;
  const ms = raw.slice(0, colon);
  if (!DECIMAL.test(ms) || !Number.isSafeInteger(Number(ms))) return null;
  const cursor = { createdAt: new Date(Number(ms)), id: raw.slice(colon + 1) };
  return encodeCursor(cursor) === text ? cursor : null;
}
