/** Las secuencias de `spec/vectors/bandeja/` contra cada almacén incluido. */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  MemoryPreferenceStore,
  MemoryStore,
  parsePolicy,
  resolveChannels,
  MemoryQuietHoursStore,
  runPreferenceVector,
  runQuietHoursVector,
  runStoreVector,
  StoreError,
  validatePatch,
  type Draft,
  type NotificationStore,
  type PreferenceStore,
} from '../src/index.js';

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

// --- preferencias guardadas --------------------------------------------------

const PREFERENCES_DIR = fileURLToPath(new URL('../../spec/vectors/preferencias-guardadas', import.meta.url));
const preferenceFiles = readdirSync(PREFERENCES_DIR)
  .filter((file) => file.endsWith('.json'))
  .sort();
const preferenceStores: Record<string, () => PreferenceStore> = { memory: () => new MemoryPreferenceStore() };

it('hay secuencias de preferencias', () => {
  expect(preferenceFiles.length).toBeGreaterThanOrEqual(2);
});

for (const [name, makeStore] of Object.entries(preferenceStores)) {
  describe(`preferencias: ${name}`, () => {
    for (const file of preferenceFiles) {
      it(file.replace('.json', ''), async () => {
        await runPreferenceVector(JSON.parse(readFileSync(join(PREFERENCES_DIR, file), 'utf8')), makeStore);
      });
    }

    it('tocar lo devuelto no cambia lo guardado', async () => {
      const store = makeStore();
      await store.saveChanges('ana', [{ category: 'activity', channel: 'email', enabled: true }]);
      const found = (await store.getOverrides('ana')) as Record<string, Record<string, boolean>>;
      found['activity']!['email'] = false;
      expect(await store.getOverrides('ana')).toEqual({ activity: { email: true } });
    });

    it('guardar una categoría `__proto__` no contamina otros objetos', async () => {
      const store = makeStore();
      await store.saveChanges('ana', [{ category: '__proto__', channel: 'polluted', enabled: true }]);
      expect(({} as Record<string, unknown>)['polluted']).toBeUndefined();
      expect(Object.keys(await store.getOverrides('ana'))).toEqual(['__proto__']);
    });

    it('lo guardado sirve para resolver canales', async () => {
      const policy = parsePolicy({
        security: { inapp: { default: true, locked: true }, email: { default: true, locked: true } },
        activity: { inapp: { default: true, locked: true }, email: { default: false } },
      });
      const store = makeStore();
      await store.saveChanges('ana', validatePatch(policy, { activity: { email: true } }, ['email']));
      expect(resolveChannels(policy, 'activity', await store.getOverrides('ana'), ['email'])).toEqual(['email']);
      expect(resolveChannels(policy, 'activity', await store.getOverrides('beto'), ['email'])).toEqual([]);
    });
  });
}

// --- horarios de no molestar -------------------------------------------------

const QUIET_DIR = fileURLToPath(new URL('../../spec/vectors/silencio-guardado', import.meta.url));
const quietFiles = readdirSync(QUIET_DIR)
  .filter((file) => file.endsWith('.json'))
  .sort();

it('hay secuencias de horarios', () => {
  expect(quietFiles.length).toBeGreaterThanOrEqual(1);
});

describe('horarios: memory', () => {
  for (const file of quietFiles) {
    it(file.replace('.json', ''), async () => {
      await runQuietHoursVector(
        JSON.parse(readFileSync(join(QUIET_DIR, file), 'utf8')),
        () => new MemoryQuietHoursStore(),
      );
    });
  }
});
