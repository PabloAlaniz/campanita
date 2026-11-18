/** Las secuencias de `spec/vectors/bandeja/` contra cada almacén incluido. */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { MemoryStore, runStoreVector, StoreError, type Draft, type NotificationStore } from '../src/index.js';

const DIR = fileURLToPath(new URL('../../spec/vectors/bandeja', import.meta.url));
const files = readdirSync(DIR)
  .filter((file) => file.endsWith('.json'))
  .sort();

const stores: Record<string, () => NotificationStore> = { memory: () => new MemoryStore() };

const draft = (changes: Partial<Draft> = {}): Draft => ({
  recipientId: 'ana',
  type: 'nota',
  category: 'activity',
  title: 'Aviso',
  body: null,
  actionUrl: null,
  payload: {},
  scope: null,
  dedupeKey: null,
  ...changes,
});
const T0 = new Date('2030-01-01T00:00:00.000Z');

it('hay secuencias', () => {
  expect(files.length).toBeGreaterThanOrEqual(8);
});

for (const [name, makeStore] of Object.entries(stores)) {
  describe(name, () => {
    for (const file of files) {
      it(file.replace('.json', ''), async () => {
        await runStoreVector(JSON.parse(readFileSync(join(DIR, file), 'utf8')), makeStore);
      });
    }

    it('el corredor avisa en qué paso falla', async () => {
      const vector = {
        id: 'prueba',
        kind: 'bandeja',
        steps: [{ note: 'contar', op: 'count_unread', recipient_id: 'ana', scope: null, cap: null, expect: 7 }],
      };
      await expect(runStoreVector(vector, makeStore)).rejects.toThrow(
        'prueba paso 0 (contar), operación `count_unread`',
      );
    });

    it('tocar el payload después no cambia lo guardado', async () => {
      const store = makeStore();
      const payload = { lista: [1, 2] };
      const added = await store.add(draft({ payload }), { id: 'n1', createdAt: T0 });
      payload.lista.push(3);
      (added.notification.payload['lista'] as number[]).push(4);
      const page = await store.listFeed({ recipientId: 'ana', limit: 5 });
      expect(page.items[0]?.payload).toEqual({ lista: [1, 2] });
    });

    it('tocar una fecha devuelta no cambia lo guardado', async () => {
      const store = makeStore();
      const added = await store.add(draft(), { id: 'n1', createdAt: T0 });
      added.notification.createdAt.setFullYear(1999);
      const page = await store.listFeed({ recipientId: 'ana', limit: 5 });
      expect(page.items[0]?.createdAt.toISOString()).toBe('2030-01-01T00:00:00.000Z');
    });

    it('una fecha inválida se rechaza', async () => {
      await expect(makeStore().add(draft(), { id: 'n1', createdAt: new Date('nope') })).rejects.toThrow(RangeError);
    });

    it('un límite que no es entero se rechaza', async () => {
      for (const limit of [2.5, Number.NaN, Number.POSITIVE_INFINITY, '3' as unknown as number]) {
        await expect(makeStore().listFeed({ recipientId: 'ana', limit })).rejects.toThrow(
          new StoreError('invalid_limit'),
        );
      }
    });

    it('una persona y una clave no se confunden con otra por cómo se combinan', async () => {
      const store = makeStore();
      const first = await store.add(draft({ recipientId: 'a', dedupeKey: 'b c' }), { id: 'n1', createdAt: T0 });
      const second = await store.add(draft({ recipientId: 'a b', dedupeKey: 'c' }), { id: 'n2', createdAt: T0 });
      expect([first.created, second.created]).toEqual([true, true]);
    });
  });
}
