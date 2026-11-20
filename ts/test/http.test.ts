/** Las secuencias de `spec/vectors/http/` contra el adaptador HTTP, sobre los almacenes en memoria. */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  MemoryPreferenceStore,
  MemoryQuietHoursStore,
  MemoryStore,
  recipientFromVectorRequest,
  runHttpVector,
} from '../src/index.js';
import { createInboxHandler, decodeCursor, encodeCursor, fromIso } from '../src/http/index.js';

const DIR = fileURLToPath(new URL('../../spec/vectors/http', import.meta.url));
const vectors = readdirSync(DIR)
  .filter((file) => file.endsWith('.json'))
  .sort()
  .map((file) => JSON.parse(readFileSync(join(DIR, file), 'utf8')));

describe('vectores http', () => {
  it.each(vectors.map((v) => [v.id, v]))('%s', async (_id, vector) => {
    await runHttpVector(vector, ({ policy, available, now }) => {
      const store = new MemoryStore();
      const handle = createInboxHandler({
        store,
        basePath: '/api/avisos',
        resolveRecipient: recipientFromVectorRequest,
        policy,
        preferences: new MemoryPreferenceStore(),
        quietHours: new MemoryQuietHoursStore(),
        available: () => available,
        now,
      });
      return { store, handle };
    });
  });
});

describe('el adaptador', () => {
  const handle = createInboxHandler({ store: new MemoryStore(), resolveRecipient: () => 'ana', basePath: '/avisos/' });

  it('deja pasar lo que no es suyo', async () => {
    expect(await handle(new Request('https://x.test/otra/feed'))).toBeNull();
    expect(await handle(new Request('https://x.test/avisos'))).toBeNull();
    expect(await handle(new Request('https://x.test/avisosfeed'))).toBeNull();
  });

  it('sin política ni almacenes no sirve preferencias ni horario', async () => {
    expect(await handle(new Request('https://x.test/avisos/preferences'))).toBeNull();
    expect(await handle(new Request('https://x.test/avisos/quiet-hours'))).toBeNull();
  });

  it('responde JSON que no se guarda en caché', async () => {
    const response = (await handle(new Request('https://x.test/avisos/feed')))!;
    expect(response.headers.get('content-type')).toBe('application/json; charset=utf-8');
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it('un id mal codificado no es una ruta', async () => {
    expect(await handle(new Request('https://x.test/avisos/feed/%E0%A4%A/read', { method: 'POST' }))).toBeNull();
  });
});

describe('el cursor y las fechas', () => {
  it('ida y vuelta, también con ids que no son ASCII', () => {
    for (const id of ['n1', 'a:b:c', 'ñandú 🐦', '']) {
      const cursor = { createdAt: new Date(1_700_000_000_123), id };
      expect(decodeCursor(encodeCursor(cursor))).toEqual(cursor);
    }
  });

  it('una fecha fuera de rango no es un cursor', () => {
    const raw = Buffer.from('8640000000000001:n').toString('base64url');
    expect(decodeCursor(raw)).toBeNull();
  });

  it('fechas estrictas', () => {
    expect(fromIso('2026-11-20T13:05:00.000Z')?.getTime()).toBe(Date.UTC(2026, 10, 20, 13, 5));
    expect(fromIso('2024-02-29T00:00:00.000Z')).not.toBeNull();
    expect(fromIso('2026-02-29T00:00:00.000Z')).toBeNull();
    expect(fromIso('0099-01-01T00:00:00.000Z')).toBeNull();
  });
});
