/** El recorrido completo contra los vectores de `spec/vectors/recorrido/`. */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  EventRejectedError,
  InvalidChannelsError,
  localMinute,
  MemoryPreferenceStore,
  MemoryQuietHoursStore,
  MemoryStore,
  Notifier,
  notificationToWire,
  parseCatalog,
  parsePolicy,
  parseQuietHours,
  isQuiet,
  StoreError,
  type Change,
  type DeliveryChannel,
  type DeliveryResult,
  type Notification,
} from '../src/index.js';

const SPEC = fileURLToPath(new URL('../../spec/vectors', import.meta.url));
const DIR = join(SPEC, 'recorrido');
const files = readdirSync(DIR)
  .filter((file) => file.endsWith('.json'))
  .sort();

/** Un canal que contesta lo que el paso le indica y anota a quién le mandó. */
class Scripted implements DeliveryChannel {
  answer = 'sent';
  sent: string[] = [];
  constructor(readonly id: string) {}

  async send(notification: Notification): Promise<DeliveryResult> {
    this.sent.push(notification.id);
    if (this.answer === 'throws') throw new Error('el proveedor no responde');
    if (this.answer === 'garbage') return 'sent' as unknown as DeliveryResult;
    if (this.answer === 'unknown') return { outcome: 'delivered' } as unknown as DeliveryResult;
    return { outcome: this.answer as DeliveryResult['outcome'] };
  }
}

type Step = Record<string, unknown>;

it('hay recorridos', () => {
  expect(files.length).toBeGreaterThanOrEqual(8);
});

describe('recorrido', () => {
  for (const file of files) {
    it(file.replace('.json', ''), async () => {
      const vector = JSON.parse(readFileSync(join(DIR, file), 'utf8')) as {
        id: string;
        policy: unknown;
        catalog: unknown;
        channels: string[];
        steps: Step[];
      };
      const policy = parsePolicy(vector.policy);
      const channels = vector.channels.map((id) => new Scripted(id));
      const preferences = new MemoryPreferenceStore();
      const quietHours = new MemoryQuietHoursStore();
      // El minuto local lo dice cada paso: la base de zonas horarias no entra en los vectores.
      let minute: number | null = null;
      // Lo que la función de disponibilidad devuelve en este paso; sin valor, la instalación no tiene.
      let available: string[] | undefined;
      const known = new Map<string, Notification>();
      const notifier = new Notifier({
        policy,
        catalog: parseCatalog(vector.catalog, policy),
        store: new MemoryStore(),
        preferences,
        channels,
        quietHours,
        localMinute: () => minute,
        available: () => available ?? vector.channels,
      });

      for (const [index, step] of vector.steps.entries()) {
        const where = `${vector.id} paso ${index} (${String(step['note'])})`;
        if (step['op'] === 'save') {
          await preferences.saveChanges(step['recipient_id'] as string, step['changes'] as Change[], {
            scope: step['scope'] as string | null,
          });
          continue;
        }
        if (step['op'] === 'quiet') {
          await quietHours.setQuietHours(step['recipient_id'] as string, parseQuietHours(step['setting']));
          continue;
        }
        minute = (step['local_minute'] as number | undefined) ?? null;
        available = step['available'] as string[] | undefined;
        const outcomes = step['outcomes'] as Record<string, string>;
        for (const channel of channels) {
          channel.answer = Object.hasOwn(outcomes, channel.id) ? outcomes[channel.id]! : 'sent';
          channel.sent = [];
        }
        let got: Record<string, unknown>;
        if (step['op'] === 'deliver') {
          const deliveries = await notifier.deliver(known.get(step['id'] as string)!);
          got = {
            attempted: [...deliveries].filter(([, d]) => d.outcome !== 'silenced').map(([id]) => id),
            deliveries: Object.fromEntries([...deliveries].map(([id, d]) => [id, d.outcome])),
          };
          expect(got, where).toEqual(step['expect']);
          const called = channels.filter((channel) => channel.sent.length > 0).map((channel) => channel.id);
          expect(called.sort(), where).toEqual([...(got['attempted'] as string[])].sort());
          continue;
        }
        try {
          const result = await notifier.notify(step['event'] as Step, {
            id: step['id'] as string,
            createdAt: new Date(step['at_ms'] as number),
            members: step['members'] as never,
          });
          known.set(result.notification.id, result.notification);
          for (const copy of result.copies) known.set(copy.notification.id, copy.notification);
          got = {
            created: result.created,
            notification: notificationToWire(result.notification),
            copies: result.copies.map((copy) => ({
              created: copy.created,
              notification: notificationToWire(copy.notification),
            })),
            attempted: [...result.deliveries]
              .filter(([, delivery]) => delivery.outcome !== 'silenced')
              .map(([id]) => id),
            deliveries: Object.fromEntries([...result.deliveries].map(([id, delivery]) => [id, delivery.outcome])),
          };
        } catch (error) {
          if (error instanceof EventRejectedError) {
            got = { error: { reason: error.reason, detail: error.detail } };
          } else if (error instanceof StoreError) {
            got = { error: { reason: error.reason, detail: null } };
          } else {
            throw error;
          }
        }
        expect(got, where).toEqual(step['expect']);
        // Lo que el recorrido dice que intentó es lo que los canales recibieron.
        const called = channels.filter((channel) => channel.sent.length > 0).map((channel) => channel.id);
        expect(called.sort(), where).toEqual([...((got['attempted'] as string[] | undefined) ?? [])].sort());
        for (const channel of channels) {
          expect(channel.sent.every((id) => id === step['id']) && channel.sent.length <= 1, where).toBe(true);
        }
      }
    });
  }
});

const base = (): { policy: ReturnType<typeof parsePolicy>; catalog: ReturnType<typeof parseCatalog> } => {
  const policy = parsePolicy({ a: { inapp: { default: true, locked: true }, email: { default: true } } });
  return { policy, catalog: parseCatalog({ nota: { category: 'a', title: 'Hola' } }, policy) };
};

it('canales configurados', () => {
  const vector = JSON.parse(readFileSync(join(SPEC, 'recorrido-canales', 'validacion.json'), 'utf8')) as {
    cases: Array<{ note: string; channels: string[]; valid: boolean }>;
  };
  for (const test of vector.cases) {
    const build = (): Notifier =>
      new Notifier({
        ...base(),
        store: new MemoryStore(),
        preferences: new MemoryPreferenceStore(),
        channels: test.channels.map((id) => new Scripted(id)),
      });
    if (test.valid) expect(build, test.note).not.toThrow();
    else expect(build, test.note).toThrow(InvalidChannelsError);
  }
});

it('sin pasar identificador ni fecha, los pone el notifier', async () => {
  const dates = [new Date('2030-01-01T00:00:00Z'), new Date('2030-01-02T00:00:00Z')];
  const notifier = new Notifier({
    ...base(),
    store: new MemoryStore(),
    preferences: new MemoryPreferenceStore(),
    channels: [new Scripted('email')],
    clock: () => dates.shift()!,
  });
  const first = await notifier.notify({ type: 'nota', recipient_id: 'ana' });
  const second = await notifier.notify({ type: 'nota', recipient_id: 'ana' });
  expect(first.notification.id).not.toBe(second.notification.id);
  expect(first.notification.id).toHaveLength(36);
  expect(second.notification.createdAt.toISOString()).toBe('2030-01-02T00:00:00.000Z');
  expect(first.deliveries.get('email')?.outcome).toBe('sent');
});

it('el error de un canal que lanza queda en el resultado', async () => {
  const email = new Scripted('email');
  email.answer = 'throws';
  const notifier = new Notifier({
    ...base(),
    store: new MemoryStore(),
    preferences: new MemoryPreferenceStore(),
    channels: [email],
  });
  const result = await notifier.notify({ type: 'nota', recipient_id: 'ana' });
  expect(result.deliveries.get('email')).toEqual({ outcome: 'failed', error: 'Error: el proveedor no responde' });
});

describe('audiencia de grupo', () => {
  const policy = parsePolicy({ a: { inapp: { default: true, locked: true }, email: { default: true } } });
  const catalog = parseCatalog(
    {
      'informe.listo': { category: 'a', title: 'Informe listo', audience: 'group' },
      nota: { category: 'a', title: 'Hola' },
    },
    policy,
  );

  it('el catálogo dice a quién le habla cada tipo', () => {
    expect(catalog['informe.listo']!.audience).toBe('group');
    expect(catalog['nota']!.audience).toBe('person');
  });

  it('sale una vez, al destino del grupo, y cada miembro ve su copia en la bandeja', async () => {
    const email = new Scripted('email');
    const store = new MemoryStore();
    const notifier = new Notifier({ policy, catalog, store, preferences: new MemoryPreferenceStore(), channels: [email] });
    const result = await notifier.notify(
      { type: 'informe.listo', recipient_id: 'equipo-1', scope: 'p-1' },
      { id: 'g1', members: [{ recipient_id: 'ana', id: 'c1' }, { recipient_id: 'beto', id: 'c2' }] },
    );
    expect(email.sent).toEqual(['g1']);
    expect(result.copies.map((c) => [c.created, c.notification.recipientId, c.notification.scope])).toEqual([
      [true, 'ana', 'p-1'],
      [true, 'beto', 'p-1'],
    ]);
    expect(await store.countUnread('ana')).toBe(1);
    expect(await store.countUnread('equipo-1')).toBe(1);
  });

  it('un tipo de persona no acepta miembros y no guarda nada', async () => {
    const store = new MemoryStore();
    const notifier = new Notifier({ policy, catalog, store, preferences: new MemoryPreferenceStore() });
    await expect(
      notifier.notify({ type: 'nota', recipient_id: 'ana' }, { members: [{ recipient_id: 'beto', id: 'c1' }] }),
    ).rejects.toEqual(new EventRejectedError('invalid_field', 'members'));
    expect(await store.countUnread('ana')).toBe(0);
  });
});

// --- no molestar: lo que depende de la plataforma ---------------------------

describe('no molestar', () => {
  const policy = parsePolicy({
    activity: {
      inapp: { default: true, locked: true },
      email: { default: true },
    },
  });
  const catalog = parseCatalog({ nota: { category: 'activity', title: 'Hola' } }, policy);
  const event = { type: 'nota', recipient_id: 'ana' };
  const setup = (options: { localMinute?: () => number | null } = {}) => {
    const channel = new Scripted('email');
    const quietHours = new MemoryQuietHoursStore();
    const notifier = new Notifier({
      policy,
      catalog,
      store: new MemoryStore(),
      preferences: new MemoryPreferenceStore(),
      channels: [channel],
      quietHours,
      ...options,
    });
    return { channel, quietHours, notifier };
  };

  it('el minuto local sale de la zona horaria', () => {
    const moment = new Date('2030-01-15T02:30:00Z');
    expect(localMinute('UTC', moment)).toBe(2 * 60 + 30);
    // Sin horario de verano: tres horas detrás de UTC todo el año.
    expect(localMinute('America/Argentina/Buenos_Aires', moment)).toBe(23 * 60 + 30);
    expect(localMinute('Asia/Tokyo', moment)).toBe(11 * 60 + 30);
    expect(localMinute('Asia/Kolkata', moment)).toBe(8 * 60);
    // Con horario de verano: la misma hora UTC cae distinto en enero y en julio.
    expect(localMinute('Europe/Madrid', moment)).toBe(3 * 60 + 30);
    expect(localMinute('Europe/Madrid', new Date('2030-07-15T02:30:00Z'))).toBe(4 * 60 + 30);
    // La medianoche es el minuto 0, no el 1440.
    expect(localMinute('UTC', new Date('2030-01-15T00:00:00Z'))).toBe(0);
  });

  it.each(['No/Existe', 'x', '../etc/passwd', 'America/', 'a'.repeat(64)])(
    'una zona que la plataforma no conoce no tiene minuto: %s',
    (zone) => {
      expect(localMinute(zone, new Date('2030-01-15T00:00:00Z'))).toBeNull();
    },
  );

  it('usa la hora local del momento en que se creó el aviso', async () => {
    const { channel, quietHours, notifier } = setup();
    await quietHours.setQuietHours(
      'ana',
      parseQuietHours({
        enabled: true,
        start: '22:00',
        end: '08:00',
        timezone: 'Asia/Tokyo',
      }),
    );
    // 15:00 UTC son las 00:00 en Tokio: de noche. 03:00 UTC son las 12:00: de día.
    const night = await notifier.notify(event, {
      createdAt: new Date('2030-01-15T15:00:00Z'),
    });
    const day = await notifier.notify(event, {
      createdAt: new Date('2030-01-15T03:00:00Z'),
    });
    expect(night.deliveries.get('email')?.outcome).toBe('silenced');
    expect(day.deliveries.get('email')?.outcome).toBe('sent');
    expect(channel.sent).toEqual([day.notification.id]);
  });

  it('con una zona desconocida se entrega', async () => {
    const { quietHours, notifier } = setup();
    await quietHours.setQuietHours(
      'ana',
      parseQuietHours({
        enabled: true,
        start: '00:00',
        end: '23:59',
        timezone: 'No/Existe',
      }),
    );
    expect((await notifier.notify(event)).deliveries.get('email')?.outcome).toBe('sent');
  });

  it('sin almacén de horarios nadie está en silencio', async () => {
    const notifier = new Notifier({
      policy,
      catalog,
      store: new MemoryStore(),
      preferences: new MemoryPreferenceStore(),
      channels: [new Scripted('email')],
      localMinute: () => 0,
    });
    expect((await notifier.notify(event)).deliveries.get('email')?.outcome).toBe('sent');
  });

  it('un canal que contesta `silenced` cuenta como falla', async () => {
    const { channel, notifier } = setup();
    channel.answer = 'silenced';
    expect((await notifier.notify(event)).deliveries.get('email')?.outcome).toBe('failed');
  });

  it.each([-1, 1440, 1.5, Number.NaN, '600', null])('isQuiet rechaza un minuto fuera del día: %s', (minute) => {
    const setting = parseQuietHours({
      enabled: true,
      start: '22:00',
      end: '08:00',
      timezone: 'UTC',
    });
    expect(() => isQuiet(setting, minute as number)).toThrow(/minuto/);
  });

  it('tocar el horario devuelto no cambia lo guardado', async () => {
    const { quietHours } = setup();
    const setting = {
      enabled: true,
      start: '22:00',
      end: '08:00',
      timezone: 'UTC',
    };
    await quietHours.setQuietHours('ana', setting);
    setting.start = '10:00';
    expect((await quietHours.getQuietHours('ana'))?.start).toBe('22:00');
  });
});

describe('deliver', () => {
  it('rechaza un aviso con una categoría que la política no tiene, sin intentar canales', async () => {
    const email = new Scripted('email');
    const store = new MemoryStore();
    const notifier = new Notifier({ ...base(), store, preferences: new MemoryPreferenceStore(), channels: [email] });
    const { notification } = await notifier.notify({ type: 'nota', recipient_id: 'ana' }, { id: 'n1' });
    email.sent = [];
    const ajeno = { ...notification, category: 'otra' };
    await expect(notifier.deliver(ajeno)).rejects.toMatchObject({ reason: 'invalid_field', detail: 'category' });
    expect(email.sent).toEqual([]);
  });

  it('mira el horario de no molestar en el instante de la llamada, no en el del aviso', async () => {
    const quietHours = new MemoryQuietHoursStore();
    await quietHours.setQuietHours('ana', parseQuietHours({ enabled: true, start: '22:00', end: '08:00', timezone: 'UTC' }));
    const moments: Date[] = [];
    let now = new Date('2030-01-01T12:00:00Z');
    const notifier = new Notifier({
      ...base(),
      store: new MemoryStore(),
      preferences: new MemoryPreferenceStore(),
      channels: [new Scripted('email')],
      quietHours,
      clock: () => now,
      localMinute: (_zone, moment) => {
        moments.push(moment);
        return moment.getUTCHours() * 60 + moment.getUTCMinutes();
      },
    });
    const { notification } = await notifier.notify({ type: 'nota', recipient_id: 'ana' }, { id: 'n1' });
    now = new Date('2030-01-01T23:00:00Z');
    const deliveries = await notifier.deliver(notification);
    expect(deliveries.get('email')).toEqual({ outcome: 'silenced' });
    expect(moments.map((m) => m.toISOString())).toEqual(['2030-01-01T12:00:00.000Z', '2030-01-01T23:00:00.000Z']);
  });

  it('acepta una disponibilidad asíncrona y le pasa el destinatario y el ámbito', async () => {
    const calls: Array<[string, string | null]> = [];
    const notifier = new Notifier({
      ...base(),
      store: new MemoryStore(),
      preferences: new MemoryPreferenceStore(),
      channels: [new Scripted('email')],
      available: async (recipientId, scope) => {
        calls.push([recipientId, scope]);
        return recipientId === 'ana' ? [] : ['email'];
      },
    });
    const ana = await notifier.notify({ type: 'nota', recipient_id: 'ana', scope: 'acme' }, { id: 'n1' });
    const beto = await notifier.notify({ type: 'nota', recipient_id: 'beto' }, { id: 'n2' });
    expect([...ana.deliveries.keys()]).toEqual([]);
    expect([...beto.deliveries.keys()]).toEqual(['email']);
    expect(calls).toEqual([
      ['ana', 'acme'],
      ['beto', null],
    ]);
  });
});
