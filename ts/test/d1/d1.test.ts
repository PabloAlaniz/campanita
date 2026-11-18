/**
 * Los almacenes de D1 contra una base D1 de verdad (la del runtime local de
 * Workers), con las mismas secuencias del spec que corre el almacén en memoria.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Miniflare } from 'miniflare';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  D1PreferenceStore,
  D1Store,
  d1Schema,
  EmailChannel,
  Notifier,
  parseCatalog,
  parsePolicy,
  runPreferenceVector,
  runStoreVector,
  StoreError,
  type D1Like,
  type Draft,
} from '../../src/index.js';

const SPEC = fileURLToPath(new URL('../../../spec/vectors', import.meta.url));
const files = (folder: string): string[] =>
  readdirSync(join(SPEC, folder))
    .filter((file) => file.endsWith('.json'))
    .sort();
const load = (folder: string, file: string): never =>
  JSON.parse(readFileSync(join(SPEC, folder, file), 'utf8')) as never;

let mf: Miniflare;
let db: D1Like;
let counter = 0;
/** Una tabla nueva por test: cada secuencia necesita un almacén vacío. */
const table = (): string => `t_${(counter += 1)}`;

const store = async (): Promise<D1Store> => {
  const made = new D1Store(db, { table: table() });
  await made.createSchema();
  return made;
};
const preferences = async (): Promise<D1PreferenceStore> => {
  const made = new D1PreferenceStore(db, { table: table() });
  await made.createSchema();
  return made;
};

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

beforeAll(async () => {
  mf = new Miniflare({
    modules: true,
    script: 'export default { fetch() { return new Response("ok"); } }',
    compatibilityDate: '2026-07-01',
    d1Databases: { DB: 'campanita-test' },
  });
  // El binding real de D1 cumple `D1Like` sin adaptar nada.
  db = await mf.getD1Database('DB');
}, 60_000);

afterAll(async () => {
  await mf?.dispose();
});

describe('D1Store', () => {
  for (const file of files('bandeja')) {
    it(`bandeja/${file.replace('.json', '')}`, async () => {
      await runStoreVector(load('bandeja', file), store);
    });
  }

  it('veinte pedidos a la vez con la misma clave dejan un solo aviso', async () => {
    const made = await store();
    const results = await Promise.all(
      Array.from({ length: 20 }, (_, index) =>
        made.add(draft({ dedupeKey: 'k', title: `intento ${index}` }), { id: `n${index}`, createdAt: T0 }),
      ),
    );
    expect(results.filter((result) => result.created)).toHaveLength(1);
    expect(new Set(results.map((result) => result.notification.id)).size).toBe(1);
    expect(await made.countUnread('ana')).toBe(1);
  });

  it('dos pedidos a la vez con el mismo identificador: uno entra y el otro se rechaza', async () => {
    const made = await store();
    const results = await Promise.allSettled([
      made.add(draft({ title: 'uno' }), { id: 'igual', createdAt: T0 }),
      made.add(draft({ title: 'dos' }), { id: 'igual', createdAt: T0 }),
    ]);
    expect(results.map((result) => result.status).sort()).toEqual(['fulfilled', 'rejected']);
    const rejected = results.find((result) => result.status === 'rejected') as PromiseRejectedResult;
    expect(rejected.reason).toEqual(new StoreError('duplicate_id'));
    expect(await made.countUnread('ana')).toBe(1);
  });

  it('el payload vuelve igual, con lo que JSON puede guardar', async () => {
    const made = await store();
    const payload = { texto: 'ñandú 🔔', lista: [1, 2.5, null, true], anidado: { a: { b: [] } }, grande: 9007199254740991 };
    await made.add(draft({ payload }), { id: 'n1', createdAt: T0 });
    const page = await made.listFeed({ recipientId: 'ana', limit: 5 });
    expect(page.items[0]?.payload).toEqual(payload);
    expect(page.items[0]?.createdAt.toISOString()).toBe('2030-01-01T00:00:00.000Z');
  });

  it('las fechas conservan los milisegundos', async () => {
    const made = await store();
    const at = new Date('2030-01-01T12:34:56.789Z');
    await made.add(draft(), { id: 'n1', createdAt: at });
    await made.markRead('n1', 'ana', new Date('2030-01-02T00:00:00.001Z'));
    const [item] = (await made.listFeed({ recipientId: 'ana', limit: 5 })).items;
    expect([item?.createdAt.toISOString(), item?.readAt?.toISOString()]).toEqual([
      '2030-01-01T12:34:56.789Z',
      '2030-01-02T00:00:00.001Z',
    ]);
  });

  it('el nombre de la tabla se valida', () => {
    expect(() => new D1Store(db, { table: 'x; DROP TABLE y' })).toThrow(/identificador/);
    expect(() => new D1PreferenceStore(db, { table: 'a b' })).toThrow(/identificador/);
    expect(() => d1Schema({ notifications: 'x-y' })).toThrow(/identificador/);
  });

  it('createSchema se puede correr dos veces', async () => {
    const made = await store();
    await made.createSchema();
    await made.add(draft(), { id: 'n1', createdAt: T0 });
    await made.createSchema();
    expect(await made.countUnread('ana')).toBe(1);
  });
});

describe('D1PreferenceStore', () => {
  for (const file of files('preferencias-guardadas')) {
    it(`preferencias-guardadas/${file.replace('.json', '')}`, async () => {
      await runPreferenceVector(load('preferencias-guardadas', file), preferences);
    });
  }

  it('guarda todos los cambios o ninguno', async () => {
    const made = await preferences();
    await made.saveChanges('ana', [{ category: 'activity', channel: 'email', enabled: true }]);
    const roto = [
      { category: 'digest', channel: 'email', enabled: false },
      { category: 'digest', channel: null as unknown as string, enabled: true },
    ];
    await expect(made.saveChanges('ana', roto)).rejects.toThrow();
    expect(await made.getOverrides('ana')).toEqual({ activity: { email: true } });
  });
});

it('el recorrido completo sobre D1', async () => {
  const policy = parsePolicy({
    security: { inapp: { default: true, locked: true }, email: { default: true, locked: true } },
    activity: { inapp: { default: true, locked: true }, email: { default: false } },
  });
  const catalog = parseCatalog(
    {
      'login.new': { category: 'security', title: 'Nuevo inicio de sesión desde {device}' },
      'comment.new': { category: 'activity', title: '{author} comentó' },
    },
    policy,
  );
  const sent: string[] = [];
  const notifications = await store();
  const saved = await preferences();
  const notifier = new Notifier({
    policy,
    catalog,
    store: notifications,
    preferences: saved,
    channels: [
      new EmailChannel({
        transport: {
          async send(message) {
            sent.push(`${message.to}: ${message.subject}`);
            return { outcome: 'sent' };
          },
        },
        sender: 'avisos@example.com',
        resolveAddress: (id) => `${id}@example.com`,
      }),
    ],
  });

  const comment = { type: 'comment.new', recipient_id: 'ana', payload: { author: 'Beto' } };
  expect((await notifier.notify(comment)).deliveries.size).toBe(0);
  await saved.saveChanges('ana', [{ category: 'activity', channel: 'email', enabled: true }]);
  expect((await notifier.notify(comment)).deliveries.get('email')?.outcome).toBe('sent');
  const login = { type: 'login.new', recipient_id: 'ana', payload: { device: 'Firefox' }, dedupe_key: 'l-1' };
  expect((await notifier.notify(login)).created).toBe(true);
  expect((await notifier.notify(login)).created).toBe(false);

  expect(sent).toEqual(['ana@example.com: Beto comentó', 'ana@example.com: Nuevo inicio de sesión desde Firefox']);
  expect(await notifications.countUnread('ana')).toBe(3);
});
