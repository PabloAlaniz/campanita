/** El recorrido completo contra los vectores de `spec/vectors/recorrido/`. */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  EventRejectedError,
  InvalidChannelsError,
  MemoryPreferenceStore,
  MemoryStore,
  Notifier,
  notificationToWire,
  parseCatalog,
  parsePolicy,
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
  expect(files.length).toBeGreaterThanOrEqual(7);
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
      const notifier = new Notifier({
        policy,
        catalog: parseCatalog(vector.catalog, policy),
        store: new MemoryStore(),
        preferences,
        channels,
      });

      for (const [index, step] of vector.steps.entries()) {
        const where = `${vector.id} paso ${index} (${String(step['note'])})`;
        if (step['op'] === 'save') {
          await preferences.saveChanges(step['recipient_id'] as string, step['changes'] as Change[], {
            scope: step['scope'] as string | null,
          });
          continue;
        }
        const outcomes = step['outcomes'] as Record<string, string>;
        for (const channel of channels) {
          channel.answer = Object.hasOwn(outcomes, channel.id) ? outcomes[channel.id]! : 'sent';
          channel.sent = [];
        }
        let got: Record<string, unknown>;
        try {
          const result = await notifier.notify(step['event'] as Step, {
            id: step['id'] as string,
            createdAt: new Date(step['at_ms'] as number),
          });
          got = {
            created: result.created,
            notification: notificationToWire(result.notification),
            attempted: [...result.deliveries.keys()],
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
