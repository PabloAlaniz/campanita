/**
 * Los almacenes de Postgres contra un Postgres de verdad, con las mismas
 * secuencias del spec que corre el almacén en memoria, por `pg` y por
 * postgres.js.
 *
 * Necesitan `CAMPANITA_PG_URL`; sin esa variable se saltean.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import postgres from 'postgres';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  EmailChannel,
  fromPostgresJs,
  Notifier,
  parseCatalog,
  parsePolicy,
  parseQuietHours,
  PostgresPreferenceStore,
  PostgresQuietHoursStore,
  PostgresStore,
  postgresSchema,
  recipientFromVectorRequest,
  runHttpVector,
  runPreferenceVector,
  runQuietHoursVector,
  runStoreVector,
  StoreError,
  type Draft,
  type PostgresLike,
} from '../../src/index.js';
import { createInboxHandler } from '../../src/http/index.js';

const URL_ = process.env.CAMPANITA_PG_URL;

const SPEC = fileURLToPath(new URL('../../../spec/vectors', import.meta.url));
const files = (folder: string): string[] =>
  readdirSync(join(SPEC, folder))
    .filter((file) => file.endsWith('.json'))
    .sort();
const load = (folder: string, file: string): never =>
  JSON.parse(readFileSync(join(SPEC, folder, file), 'utf8')) as never;

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

// El `Client` de `pg` también cumple `PostgresLike` sin adaptar nada; lo revisa `tsc`.
void ((): PostgresLike => new pg.Client());

/** Un esquema propio por corrida, para no pisar nada de la base. */
const SCHEMA = `campanita_test_${process.pid}_${Date.now()}`;

interface Driver {
  name: string;
  open(): Promise<{ db: PostgresLike; close(): Promise<void> }>;
}

const drivers: Driver[] = [
  {
    name: 'pg',
    async open() {
      const pool = new pg.Pool({ connectionString: URL_, options: `-c search_path=${SCHEMA}` });
      // El Pool de `pg` cumple `PostgresLike` sin adaptar nada.
      return { db: pool, close: () => pool.end() };
    },
  },
  {
    name: 'postgres.js',
    async open() {
      const sql = postgres(URL_!, {
        connection: { search_path: SCHEMA },
        onnotice: () => {},
      });
      return { db: fromPostgresJs(sql), close: () => sql.end() };
    },
  },
];

describe('Postgres sin base', () => {
  it('si el driver no informa cuántas filas cambió, rechaza en vez de suponer cero', async () => {
    const db: PostgresLike = { query: async () => ({ rows: [], rowCount: null }) };
    const store = new PostgresStore(db);
    await expect(store.add(draft(), { id: 'n1', createdAt: T0 })).rejects.toThrow(/filas/);
    await expect(store.markRead('n1', 'ana', T0)).rejects.toThrow(/filas/);
    await expect(store.markAllRead('ana', { before: T0, readAt: T0 })).rejects.toThrow(/filas/);
  });
});

describe.skipIf(URL_ === undefined)('Postgres', () => {
  beforeAll(async () => {
    const admin = new pg.Client({ connectionString: URL_ });
    await admin.connect();
    await admin.query(`CREATE SCHEMA ${SCHEMA}`);
    await admin.end();
  });

  afterAll(async () => {
    const admin = new pg.Client({ connectionString: URL_ });
    await admin.connect();
    await admin.query(`DROP SCHEMA ${SCHEMA} CASCADE`);
    await admin.end();
  });

  describe.each(drivers)('por $name', (driver) => {
    let db: PostgresLike;
    let close: () => Promise<void>;
    let counter = 0;
    /** Una tabla nueva por test: cada secuencia necesita un almacén vacío. */
    const table = (): string => `t_${driver.name.replace(/\W/g, '')}_${(counter += 1)}`;

    const store = async (): Promise<PostgresStore> => {
      const made = new PostgresStore(db, { table: table() });
      await made.createSchema();
      return made;
    };
    const preferences = async (): Promise<PostgresPreferenceStore> => {
      const made = new PostgresPreferenceStore(db, { table: table() });
      await made.createSchema();
      return made;
    };
    const quietHours = async (): Promise<PostgresQuietHoursStore> => {
      const made = new PostgresQuietHoursStore(db, { table: table() });
      await made.createSchema();
      return made;
    };

    beforeAll(async () => {
      ({ db, close } = await driver.open());
    });

    afterAll(async () => {
      await close?.();
    });

    describe('PostgresStore', () => {
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

      it('a igual fecha, ordena el id por punto de código y no por la colación de la base', async () => {
        const made = await store();
        // Por punto de código: "B" < "a" < "b" < "ä" < "😀". Con en_US saldría a, ä, b, B, ...
        const ids = ['a', 'B', 'ä', 'b', '😀'];
        for (const id of ids) await made.add(draft(), { id, createdAt: T0 });
        const first = await made.listFeed({ recipientId: 'ana', limit: 2 });
        const rest = await made.listFeed({ recipientId: 'ana', limit: 5, cursor: first.nextCursor });
        expect([...first.items, ...rest.items].map((item) => item.id)).toEqual(['😀', 'ä', 'b', 'a', 'B']);
      });

      it('el payload vuelve igual, con lo que JSON puede guardar', async () => {
        const made = await store();
        const payload = {
          texto: 'ñandú 🔔',
          nulo: '\u0000',
          lista: [1, 2.5, null, true],
          anidado: { a: { b: [] } },
          grande: 9007199254740991,
        };
        await made.add(draft({ payload }), { id: 'n1', createdAt: T0 });
        const page = await made.listFeed({ recipientId: 'ana', limit: 5 });
        expect(page.items[0]?.payload).toEqual(payload);
        expect(page.items[0]?.createdAt.toISOString()).toBe('2030-01-01T00:00:00.000Z');
      });

      it('las fechas conservan los milisegundos, también lejos de 1970', async () => {
        const made = await store();
        const at = new Date('2999-12-31T23:59:59.999Z');
        await made.add(draft(), { id: 'n1', createdAt: at });
        await made.markRead('n1', 'ana', new Date('3000-01-01T00:00:00.001Z'));
        const [item] = (await made.listFeed({ recipientId: 'ana', limit: 5 })).items;
        expect([item?.createdAt.toISOString(), item?.readAt?.toISOString()]).toEqual([
          '2999-12-31T23:59:59.999Z',
          '3000-01-01T00:00:00.001Z',
        ]);
      });

      it('el nombre de la tabla se valida', () => {
        expect(() => new PostgresStore(db, { table: 'x; DROP TABLE y' })).toThrow(/identificador/);
        expect(() => new PostgresPreferenceStore(db, { table: 'a b' })).toThrow(/identificador/);
        expect(() => postgresSchema({ notifications: 'x-y' })).toThrow(/identificador/);
        expect(() => new PostgresQuietHoursStore(db, { table: 'ñandú' })).toThrow(/identificador/);
        expect(() => new PostgresStore(db, { table: 'x'.repeat(57) })).toThrow(/56/);
      });

      it('con el nombre más largo que acepta, los índices igual se crean', async () => {
        const name = `${table()}_`.padEnd(56, 'x');
        await new PostgresStore(db, { table: name }).createSchema();
        const found = await db.query(
          'SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND tablename = $2 ORDER BY indexname',
          [SCHEMA, name],
        );
        expect(found.rows).toEqual([
          { indexname: `${name}_dedupe` },
          { indexname: `${name}_feed` },
          { indexname: `${name}_pkey` },
        ]);
      });

      it('createSchema se puede correr dos veces', async () => {
        const made = await store();
        await made.createSchema();
        await made.add(draft(), { id: 'n1', createdAt: T0 });
        await made.createSchema();
        expect(await made.countUnread('ana')).toBe(1);
      });
    });

    describe('PostgresPreferenceStore', () => {
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

      it('categorías y canales con lo que un arreglo de Postgres escapa', async () => {
        const made = await preferences();
        const raros = ['NULL', 'a,b', '"comillas"', 'barra\\', '{llaves}', ' espacio ', '', 'ñandú 🔔'];
        const changes = raros.map((text, index) => ({ category: text, channel: text, enabled: index % 2 === 0 }));
        await made.saveChanges('ana', changes, { scope: '' });
        const expected = Object.fromEntries(changes.map((c) => [c.category, { [c.channel]: c.enabled }]));
        expect(await made.getOverrides('ana', { scope: '' })).toEqual(expected);
        expect(await made.getOverrides('ana')).toEqual({});
      });

      it('si una celda se repite en los mismos cambios, vale la última', async () => {
        const made = await preferences();
        await made.saveChanges('ana', [
          { category: 'activity', channel: 'email', enabled: true },
          { category: 'activity', channel: 'sms', enabled: true },
          { category: 'activity', channel: 'email', enabled: false },
        ]);
        expect(await made.getOverrides('ana')).toEqual({ activity: { email: false, sms: true } });
      });
    });

    describe('PostgresQuietHoursStore', () => {
      for (const file of files('silencio-guardado')) {
        it(`silencio-guardado/${file.replace('.json', '')}`, async () => {
          await runQuietHoursVector(load('silencio-guardado', file), quietHours);
        });
      }

      it('createSchema se puede correr dos veces', async () => {
        const made = await quietHours();
        await made.setQuietHours(
          'ana',
          parseQuietHours({ enabled: true, start: '22:00', end: '08:00', timezone: 'UTC' }),
        );
        await made.createSchema();
        expect((await made.getQuietHours('ana'))?.start).toBe('22:00');
      });
    });

    it('el recorrido completo', async () => {
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

      expect(sent).toEqual([
        'ana@example.com: Beto comentó',
        'ana@example.com: Nuevo inicio de sesión desde Firefox',
      ]);
      expect(await notifications.countUnread('ana')).toBe(3);
    });

    describe('la bandeja por HTTP', () => {
      it.each(files('http'))('%s', async (file) => {
        await runHttpVector(load('http', file), async ({ policy, available, now }) => {
          const notifications = await store();
          const handle = createInboxHandler({
            store: notifications,
            basePath: '/api/avisos',
            resolveRecipient: recipientFromVectorRequest,
            policy,
            preferences: await preferences(),
            quietHours: await quietHours(),
            available: () => available,
            now,
          });
          return { store: notifications, handle };
        });
      });
    });
  });
});
