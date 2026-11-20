/**
 * El estado de una campanita, sin framework: la primera página de la bandeja,
 * el contador y las acciones, con `subscribe` y `getSnapshot` para conectarlo a
 * cualquier interfaz (React lo hace con `campanita/react`).
 *
 * Mientras alguien escucha, pide el contador cada `pollMs` (una consulta
 * barata) y vuelve a pedir la página sólo cuando el contador cambia. Marcar
 * como leído es optimista: la interfaz cambia en el acto y vuelve atrás si el
 * servidor falla.
 */
import type { InboxItem, UnreadCountResponse } from '../http/wire.js';
import type { InboxClient } from './client.js';

export interface InboxSnapshot {
  readonly items: readonly InboxItem[];
  readonly unread: UnreadCountResponse;
  /** `loading` sólo la primera vez: después se refresca sin vaciar la lista. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** El último error, también el de una acción que se deshizo. */
  readonly error: Error | null;
  readonly hasMore: boolean;
  readonly loadingMore: boolean;
}

export interface InboxStoreOptions {
  /** El ámbito de la bandeja: lo de ese ámbito y lo de cuenta. Sin ámbito, todo. */
  readonly scope?: string | null;
  /** Avisos por página. Por omisión, 20. */
  readonly pageSize?: number;
  /** Cada cuánto se mira el contador, en milisegundos. `0` no mira solo. Por omisión, 30 s. */
  readonly pollMs?: number;
  /** El tope del contador. Por omisión, 99. */
  readonly cap?: number;
}

export interface InboxStore {
  subscribe(listener: () => void): () => void;
  getSnapshot(): InboxSnapshot;
  /** Pide el contador y la primera página. */
  refresh(): Promise<void>;
  loadMore(): Promise<void>;
  markRead(id: string): Promise<void>;
  /** Marca lo que la persona tiene a la vista, no lo que llegó después. */
  markAllRead(): Promise<void>;
  /** Deja de mirar el contador. Lo vuelve a hacer quien se suscriba de nuevo. */
  stop(): void;
}

const INITIAL: InboxSnapshot = Object.freeze({
  items: [],
  unread: { count: 0, capped: false },
  status: 'idle',
  error: null,
  hasMore: false,
  loadingMore: false,
});

const asError = (error: unknown): Error => (error instanceof Error ? error : new Error(String(error)));
const afterMs = (iso: string): string => new Date(Date.parse(iso) + 1).toISOString();

export function createInboxStore(client: InboxClient, options: InboxStoreOptions = {}): InboxStore {
  const scope = options.scope ?? null;
  const pageSize = options.pageSize ?? 20;
  const pollMs = options.pollMs ?? 30_000;
  const cap = options.cap ?? 99;

  let snapshot = INITIAL;
  let cursor: string | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  /** Sube con cada acción: una lectura que salió antes no pisa lo que la persona hizo después. */
  let epoch = 0;
  let inFlight: Promise<void> | null = null;
  const listeners = new Set<() => void>();

  const set = (change: Partial<InboxSnapshot>): void => {
    snapshot = { ...snapshot, ...change };
    for (const listener of listeners) listener();
  };

  async function load(): Promise<void> {
    const started = epoch;
    if (snapshot.status === 'idle') set({ status: 'loading' });
    try {
      const [unread, page] = await Promise.all([
        client.unreadCount({ scope, cap }),
        client.feed({ scope, limit: pageSize }),
      ]);
      if (started !== epoch) return load();
      cursor = page.next_cursor;
      set({ unread, items: page.items, hasMore: cursor !== null, status: 'ready', error: null });
    } catch (error) {
      set({ status: snapshot.status === 'loading' ? 'error' : snapshot.status, error: asError(error) });
    }
  }

  function refresh(): Promise<void> {
    inFlight ??= load().finally(() => {
      inFlight = null;
    });
    return inFlight;
  }

  async function poll(): Promise<void> {
    if (inFlight) return;
    const started = epoch;
    try {
      const unread = await client.unreadCount({ scope, cap });
      if (started !== epoch) return;
      const { count, capped } = snapshot.unread;
      if (snapshot.status !== 'ready' || unread.count !== count || unread.capped !== capped) await refresh();
    } catch (error) {
      set({ error: asError(error) });
    }
  }

  function start(): void {
    if (timer !== null || pollMs <= 0) return;
    timer = setInterval(() => void poll(), pollMs);
  }

  function stop(): void {
    if (timer !== null) clearInterval(timer);
    timer = null;
  }

  async function optimistic(change: () => Partial<InboxSnapshot>, send: () => Promise<unknown>): Promise<void> {
    epoch += 1;
    const before = { items: snapshot.items, unread: snapshot.unread };
    set(change());
    try {
      await send();
    } catch (error) {
      epoch += 1;
      set({ ...before, error: asError(error) });
      return;
    }
    await refresh();
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) {
        start();
        if (snapshot.status === 'idle') void refresh();
      }
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) stop();
      };
    },
    getSnapshot: () => snapshot,
    refresh,
    stop,

    async loadMore() {
      if (cursor === null || snapshot.loadingMore) return;
      const started = epoch;
      set({ loadingMore: true });
      try {
        const page = await client.feed({ scope, limit: pageSize, cursor });
        if (started !== epoch) {
          set({ loadingMore: false });
          return;
        }
        const seen = new Set(snapshot.items.map((item) => item.id));
        cursor = page.next_cursor;
        set({
          items: [...snapshot.items, ...page.items.filter((item) => !seen.has(item.id))],
          hasMore: cursor !== null,
          loadingMore: false,
        });
      } catch (error) {
        set({ loadingMore: false, error: asError(error) });
      }
    },

    async markRead(id) {
      const target = snapshot.items.find((item) => item.id === id);
      if (target === undefined || target.read_at !== null) return;
      const now = new Date().toISOString();
      await optimistic(
        () => ({
          items: snapshot.items.map((item) => (item.id === id ? { ...item, read_at: now } : item)),
          unread: snapshot.unread.capped
            ? snapshot.unread
            : { count: Math.max(0, snapshot.unread.count - 1), capped: false },
        }),
        () => client.markRead(id),
      );
    },

    async markAllRead() {
      const newest = snapshot.items[0];
      if (newest === undefined) return;
      const before = afterMs(newest.created_at);
      const now = new Date().toISOString();
      await optimistic(
        () => ({
          items: snapshot.items.map((item) => (item.read_at === null ? { ...item, read_at: now } : item)),
          unread: { count: 0, capped: false },
        }),
        () => client.markAllRead({ before, scope }),
      );
    },
  };
}
