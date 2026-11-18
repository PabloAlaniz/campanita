/**
 * Corre las secuencias del spec contra un almacén.
 *
 * Es lo que usa este paquete para probar sus adaptadores, y lo que puede usar
 * quien escriba el suyo:
 *
 *     const vector = JSON.parse(readFileSync('spec/vectors/bandeja/basico.json', 'utf8'));
 *     await runStoreVector(vector, () => new MiAlmacen(...));
 *
 * Cada vector necesita un almacén vacío.
 */
import { StoreError, type Notification } from './core/inbox.js';
import type { Draft } from './core/types.js';
import type { NotificationStore } from './ports.js';

type Wire = Record<string, unknown>;

/** El aviso con los nombres y las fechas que usan los vectores. */
export function notificationToWire(item: Notification): Wire {
  return {
    id: item.id,
    recipient_id: item.recipientId,
    type: item.type,
    category: item.category,
    title: item.title,
    body: item.body,
    action_url: item.actionUrl,
    payload: item.payload,
    scope: item.scope,
    dedupe_key: item.dedupeKey,
    created_at_ms: item.createdAt.getTime(),
    read_at_ms: item.readAt === null ? null : item.readAt.getTime(),
  };
}

function draftFromWire(wire: Wire): Draft {
  return {
    recipientId: wire['recipient_id'] as string,
    type: wire['type'] as string,
    category: wire['category'] as string,
    title: wire['title'] as string,
    body: wire['body'] as string | null,
    actionUrl: wire['action_url'] as string | null,
    payload: wire['payload'] as Record<string, unknown>,
    scope: wire['scope'] as string | null,
    dedupeKey: wire['dedupe_key'] as string | null,
  };
}

/**
 * Corre un vector de tipo `bandeja`. Lanza un `Error` en el primer paso que no
 * coincide, diciendo cuál.
 */
export async function runStoreVector(
  vector: { readonly id: string; readonly kind: string; readonly steps: readonly Wire[] },
  makeStore: () => NotificationStore | Promise<NotificationStore>,
): Promise<void> {
  if (vector.kind !== 'bandeja') {
    throw new TypeError('runStoreVector sólo corre vectores de tipo `bandeja`');
  }
  const store = await makeStore();
  for (const [index, step] of vector.steps.entries()) {
    let got: unknown;
    try {
      got = await run(store, step);
    } catch (error) {
      if (!(error instanceof StoreError)) throw error;
      got = { error: error.reason };
    }
    if (!sameJson(got, step['expect'])) {
      throw new Error(
        `${vector.id} paso ${index} (${String(step['note'])}), operación \`${String(step['op'])}\`:\n` +
          `  se esperaba: ${JSON.stringify(step['expect'])}\n` +
          `  se obtuvo:   ${JSON.stringify(got)}`,
      );
    }
  }
}

async function run(store: NotificationStore, step: Wire): Promise<unknown> {
  const text = (key: string): string => step[key] as string;
  const at = (key: string): Date => new Date(step[key] as number);
  switch (step['op']) {
    case 'add': {
      const result = await store.add(draftFromWire(step['draft'] as Wire), { id: text('id'), createdAt: at('at_ms') });
      return { created: result.created, notification: notificationToWire(result.notification) };
    }
    case 'list': {
      const query = step['query'] as Wire;
      const cursor = (query['cursor'] ?? null) as { created_at_ms: number; id: string } | null;
      const page = await store.listFeed({
        recipientId: query['recipient_id'] as string,
        limit: query['limit'] as number,
        cursor: cursor === null ? null : { createdAt: new Date(cursor.created_at_ms), id: cursor.id },
        unreadOnly: query['unread_only'] === true,
        category: (query['category'] ?? null) as string | null,
        scope: (query['scope'] ?? null) as string | null,
      });
      return {
        items: page.items.map(notificationToWire),
        next_cursor:
          page.nextCursor === null
            ? null
            : { created_at_ms: page.nextCursor.createdAt.getTime(), id: page.nextCursor.id },
      };
    }
    case 'count_unread':
      return store.countUnread(text('recipient_id'), {
        scope: step['scope'] as string | null,
        cap: step['cap'] as number | null,
      });
    case 'mark_read':
      return store.markRead(text('id'), text('recipient_id'), at('at_ms'));
    case 'mark_all_read':
      return store.markAllRead(text('recipient_id'), {
        before: at('before_ms'),
        readAt: at('at_ms'),
        scope: step['scope'] as string | null,
        category: step['category'] as string | null,
      });
    default:
      throw new TypeError(`operación desconocida: ${String(step['op'])}`);
  }
}

/** Igualdad de valores JSON, sin depender del orden de las claves. */
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((value, index) => sameJson(value, b[index]));
  }
  const left = a as Wire;
  const right = b as Wire;
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((key) => Object.hasOwn(right, key) && sameJson(left[key], right[key]))
  );
}
