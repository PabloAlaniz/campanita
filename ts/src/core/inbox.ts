/** La bandeja: el aviso guardado y lo que se le puede pedir a un almacén. */
import type { Draft } from './types.js';
export { byCodePoint } from './order.js';

/** Un aviso guardado. `readAt` es lo único que cambia después de creado. */
export interface Notification extends Draft {
  readonly id: string;
  readonly createdAt: Date;
  readonly readAt: Date | null;
}

export interface AddResult {
  readonly notification: Notification;
  /** `false` cuando la clave ya existía: `notification` es el aviso que había. */
  readonly created: boolean;
}

/** La posición del último aviso de una página. */
export interface FeedCursor {
  readonly createdAt: Date;
  readonly id: string;
}

export interface FeedQuery {
  readonly recipientId: string;
  /** Cuántos avisos como máximo. Lo decide quien muestra la bandeja. */
  readonly limit: number;
  readonly cursor?: FeedCursor | null;
  readonly unreadOnly?: boolean;
  readonly category?: string | null;
  /** Con ámbito: lo de ese ámbito y lo que no tiene ámbito. Sin ámbito: todo. */
  readonly scope?: string | null;
}

export interface FeedPage {
  readonly items: readonly Notification[];
  /** `null` cuando no queda nada más. */
  readonly nextCursor: FeedCursor | null;
}

/**
 * Una página a partir de hasta `limit + 1` avisos ya ordenados del más nuevo al
 * más viejo. El de más sólo dice si queda otra página: así no hace falta contar.
 */
export function paginate(rows: readonly Notification[], limit: number): FeedPage {
  const items = rows.slice(0, limit);
  const last = items.at(-1);
  return {
    items,
    nextCursor: last !== undefined && rows.length > limit ? { createdAt: new Date(last.createdAt), id: last.id } : null,
  };
}

/** Por qué un almacén rechaza una operación. Son los literales del spec. */
export type StoreReason = 'duplicate_id' | 'invalid_limit' | 'invalid_cap';

const MOTIVOS: Record<StoreReason, string> = {
  duplicate_id: 'ya hay un aviso con ese identificador',
  invalid_limit: 'el límite tiene que ser un entero mayor que cero',
  invalid_cap: 'el tope tiene que ser un entero mayor que cero',
};

/** El almacén rechazó la operación. No cambió nada. */
export class StoreError extends Error {
  override readonly name = 'StoreError';

  constructor(readonly reason: StoreReason) {
    super(MOTIVOS[reason]);
  }
}

export function checkLimit(limit: unknown): number {
  if (typeof limit !== 'number' || !Number.isSafeInteger(limit) || limit < 1) {
    throw new StoreError('invalid_limit');
  }
  return limit;
}

export function checkCap(cap: unknown): number | null {
  if (cap === null || cap === undefined) return null;
  if (typeof cap !== 'number' || !Number.isSafeInteger(cap) || cap < 1) {
    throw new StoreError('invalid_cap');
  }
  return cap;
}

/**
 * Milisegundos desde 1970. Las fechas del spec tienen esa resolución, que es la
 * de `Date`. Una fecha inválida no se guarda.
 */
export function toMs(moment: Date): number {
  const ms = moment.getTime();
  if (Number.isNaN(ms)) throw new RangeError('la fecha no es válida');
  return ms;
}

