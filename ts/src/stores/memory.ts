/** Un almacén en memoria. Sirve para tests y para probar campanita sin base. */
import {
  byCodePoint,
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

/** Todo vive en el proceso y se pierde al terminar. */
export class MemoryStore implements NotificationStore {
  readonly #byId = new Map<string, Notification>();
  /** Persona → clave → identificador. Dos niveles: ningún separador puede chocar. */
  readonly #byKey = new Map<string, Map<string, string>>();

  async add(draft: Draft, options: { id: string; createdAt: Date }): Promise<AddResult> {
    const keys = this.#byKey.get(draft.recipientId);
    if (draft.dedupeKey !== null) {
      const existing = keys?.get(draft.dedupeKey);
      if (existing !== undefined) {
        return { notification: copy(this.#byId.get(existing)!), created: false };
      }
    }
    if (this.#byId.has(options.id)) throw new StoreError('duplicate_id');

    const stored: Notification = {
      ...draft,
      // Una copia profunda: lo guardado no cambia si quien llama toca su payload.
      payload: structuredClone(draft.payload),
      id: options.id,
      createdAt: new Date(toMs(options.createdAt)),
      readAt: null,
    };
    this.#byId.set(stored.id, stored);
    if (draft.dedupeKey !== null) {
      if (keys === undefined) this.#byKey.set(draft.recipientId, new Map([[draft.dedupeKey, stored.id]]));
      else keys.set(draft.dedupeKey, stored.id);
    }
    return { notification: copy(stored), created: true };
  }

  async listFeed(query: FeedQuery): Promise<FeedPage> {
    const limit = checkLimit(query.limit);
    const category = query.category ?? null;
    let rows = this.#visible(query.recipientId, query.scope ?? null).filter(
      (item) => (!query.unreadOnly || item.readAt === null) && (category === null || item.category === category),
    );
    rows.sort((a, b) => -compare(a, b));
    const cursor = query.cursor ?? null;
    if (cursor !== null) {
      const edge = { createdAt: new Date(toMs(cursor.createdAt)), id: cursor.id };
      rows = rows.filter((item) => compare(item, edge) < 0);
    }
    const page = rows.slice(0, limit);
    const last = page.at(-1);
    return {
      items: page.map(copy),
      nextCursor:
        last !== undefined && rows.length > limit ? { createdAt: new Date(last.createdAt), id: last.id } : null,
    };
  }

  async countUnread(
    recipientId: string,
    options: { scope?: string | null; cap?: number | null } = {},
  ): Promise<number> {
    const cap = checkCap(options.cap);
    const total = this.#visible(recipientId, options.scope ?? null).filter((item) => item.readAt === null).length;
    return cap === null ? total : Math.min(total, cap);
  }

  async markRead(id: string, recipientId: string, readAt: Date): Promise<boolean> {
    const item = this.#byId.get(id);
    if (item === undefined || item.recipientId !== recipientId || item.readAt !== null) return false;
    this.#byId.set(id, { ...item, readAt: new Date(toMs(readAt)) });
    return true;
  }

  async markAllRead(
    recipientId: string,
    options: { before: Date; readAt: Date; scope?: string | null; category?: string | null },
  ): Promise<number> {
    const cut = toMs(options.before);
    const at = toMs(options.readAt);
    const category = options.category ?? null;
    let changed = 0;
    for (const item of this.#visible(recipientId, options.scope ?? null)) {
      if (item.readAt !== null || item.createdAt.getTime() >= cut) continue;
      if (category !== null && item.category !== category) continue;
      this.#byId.set(item.id, { ...item, readAt: new Date(at) });
      changed += 1;
    }
    return changed;
  }

  #visible(recipientId: string, scope: string | null): Notification[] {
    return [...this.#byId.values()].filter(
      (item) => item.recipientId === recipientId && (scope === null || item.scope === null || item.scope === scope),
    );
  }
}

function compare(a: { createdAt: Date; id: string }, b: { createdAt: Date; id: string }): number {
  return a.createdAt.getTime() - b.createdAt.getTime() || byCodePoint(a.id, b.id);
}

/** Lo que sale del almacén es una copia: tocarlo no cambia lo guardado. */
function copy(item: Notification): Notification {
  return {
    ...item,
    payload: structuredClone(item.payload),
    createdAt: new Date(item.createdAt),
    readAt: item.readAt === null ? null : new Date(item.readAt),
  };
}

/** Las preferencias guardadas, en el proceso. */
export class MemoryPreferenceStore implements PreferenceStore {
  /**
   * Persona → ámbito → categoría → canal. Son `Map` y no objetos: una persona o
   * una categoría llamada `__proto__` es un dato como cualquier otro, y ningún
   * separador puede hacer chocar dos claves.
   */
  readonly #cells = new Map<string, Map<string | null, Map<string, Map<string, boolean>>>>();

  async getOverrides(recipientId: string, options: { scope?: string | null } = {}): Promise<Overrides> {
    const mine = this.#cells.get(recipientId)?.get(options.scope ?? null);
    // Sin prototipo, para que una categoría `__proto__` sea una clave más.
    const out: Record<string, Record<string, boolean>> = Object.create(null);
    for (const [category, channels] of mine ?? []) {
      const row: Record<string, boolean> = Object.create(null);
      for (const [channel, enabled] of channels) row[channel] = enabled;
      out[category] = row;
    }
    return out;
  }

  async saveChanges(
    recipientId: string,
    changes: readonly Change[],
    options: { scope?: string | null } = {},
  ): Promise<void> {
    if (changes.length === 0) return;
    const scopes = getOrCreate(this.#cells, recipientId);
    const categories = getOrCreate(scopes, options.scope ?? null);
    for (const change of changes) {
      getOrCreate(categories, change.category).set(change.channel, change.enabled);
    }
  }
}

/** Los horarios de no molestar, en el proceso. */
export class MemoryQuietHoursStore implements QuietHoursStore {
  readonly #byRecipient = new Map<string, QuietHours>();

  async getQuietHours(recipientId: string): Promise<QuietHours | null> {
    return this.#byRecipient.get(recipientId) ?? null;
  }

  async setQuietHours(recipientId: string, quietHours: QuietHours): Promise<void> {
    this.#byRecipient.set(recipientId, { ...quietHours });
  }
}

function getOrCreate<K, IK, IV>(map: Map<K, Map<IK, IV>>, key: K): Map<IK, IV> {
  let inner = map.get(key);
  if (inner === undefined) {
    inner = new Map();
    map.set(key, inner);
  }
  return inner;
}
