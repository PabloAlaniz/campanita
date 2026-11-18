/** El recorrido completo: armar el aviso, guardarlo y entregarlo. */
import type { DeliveryChannel, DeliveryResult, NotificationStore, PreferenceStore } from '../ports.js';
import { buildNotification } from './catalog.js';
import { resolveChannels } from './channels.js';
import type { Notification } from './inbox.js';
import { INBOX, type Catalog, type Policy } from './types.js';

const ID = /^[a-z][a-z0-9_-]{0,63}$/;
const OUTCOMES = new Set(['sent', 'expired', 'failed']);

/** Los canales configurados no cumplen el spec. Se detecta al armar el `Notifier`. */
export class InvalidChannelsError extends Error {
  override readonly name = 'InvalidChannelsError';
}

export interface NotifyResult {
  readonly notification: Notification;
  /** `false` cuando la clave ya existía: no se guardó nada nuevo ni se entregó. */
  readonly created: boolean;
  /** Qué pasó en cada canal que se intentó, en el orden en que se intentaron. */
  readonly deliveries: ReadonlyMap<string, DeliveryResult>;
}

export interface NotifierOptions {
  readonly policy: Policy;
  readonly catalog: Catalog;
  readonly store: NotificationStore;
  readonly preferences: PreferenceStore;
  readonly channels?: Iterable<DeliveryChannel>;
  /** Por omisión, `crypto.randomUUID()`. */
  readonly newId?: () => string;
  /** Por omisión, `new Date()`. */
  readonly clock?: () => Date;
}

/**
 * Junta la política, el catálogo, los almacenes y los canales de una instalación.
 *
 * Es lo único que hace falta para avisar: `await notifier.notify(evento)`.
 */
export class Notifier {
  readonly #options: NotifierOptions;
  readonly #channels: Map<string, DeliveryChannel>;

  constructor(options: NotifierOptions) {
    this.#options = options;
    this.#channels = checkChannels(options.channels ?? []);
  }

  /**
   * Arma el aviso del evento, lo guarda y lo entrega.
   *
   * Rechaza con `EventRejectedError` si el evento no se puede convertir en un
   * aviso (y entonces no se guardó nada) y con `StoreError` si el almacén lo
   * rechaza. Una entrega que falla nunca rechaza: queda anotada en `deliveries`.
   */
  async notify(
    event: Readonly<Record<string, unknown>>,
    options: { id?: string; createdAt?: Date } = {},
  ): Promise<NotifyResult> {
    const { policy, catalog, store, preferences } = this.#options;
    const draft = buildNotification(catalog, event);
    const added = await store.add(draft, {
      id: options.id ?? (this.#options.newId ?? (() => crypto.randomUUID()))(),
      createdAt: options.createdAt ?? (this.#options.clock ?? (() => new Date()))(),
    });
    const deliveries = new Map<string, DeliveryResult>();
    // El aviso ya existía: no se vuelve a entregar. Reintentar una entrega
    // fallida es decisión de quien llama.
    if (!added.created) return { notification: added.notification, created: false, deliveries };

    const overrides = await preferences.getOverrides(draft.recipientId, { scope: draft.scope });
    // `resolveChannels` ya los devuelve ordenados por punto de código.
    for (const channelId of resolveChannels(policy, draft.category, overrides, this.#channels.keys())) {
      deliveries.set(channelId, await deliver(this.#channels.get(channelId)!, added.notification));
    }
    return { notification: added.notification, created: true, deliveries };
  }
}

function checkChannels(channels: Iterable<DeliveryChannel>): Map<string, DeliveryChannel> {
  const byId = new Map<string, DeliveryChannel>();
  for (const channel of channels) {
    const id: unknown = channel.id;
    if (typeof id !== 'string' || !ID.test(id)) {
      throw new InvalidChannelsError(`canal ${JSON.stringify(id)}: no es un identificador`);
    }
    if (id === INBOX) {
      throw new InvalidChannelsError(`\`${INBOX}\` es la bandeja, no un canal de entrega`);
    }
    if (byId.has(id)) throw new InvalidChannelsError(`canal \`${id}\` repetido`);
    byId.set(id, channel);
  }
  return byId;
}

/** Un canal no debería lanzar, pero si lanza no puede cortar a los demás. */
async function deliver(channel: DeliveryChannel, notification: Notification): Promise<DeliveryResult> {
  let result: unknown;
  try {
    result = await channel.send(notification);
  } catch (error) {
    const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
    return { outcome: 'failed', error: text };
  }
  if (typeof result !== 'object' || result === null || !OUTCOMES.has((result as DeliveryResult).outcome)) {
    return { outcome: 'failed', error: 'el canal no devolvió un resultado válido' };
  }
  return result as DeliveryResult;
}
