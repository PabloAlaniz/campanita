/** El recorrido completo: armar el aviso, guardarlo y entregarlo. */
import type { DeliveryChannel, DeliveryResult, NotificationStore, PreferenceStore, QuietHoursStore } from '../ports.js';
import { buildNotification } from './catalog.js';
import { resolveChannels } from './channels.js';
import type { Notification } from './inbox.js';
import { isQuiet, localMinute } from './quiet.js';
import { INBOX, type Catalog, type Policy } from './types.js';

const ID = /^[a-z][a-z0-9_-]{0,63}$/;
const OUTCOMES = new Set(['sent', 'expired', 'failed', 'skipped']);

/** Los canales configurados no cumplen el spec. Se detecta al armar el `Notifier`. */
export class InvalidChannelsError extends Error {
  override readonly name = 'InvalidChannelsError';
}

export interface NotifyResult {
  readonly notification: Notification;
  /** `false` cuando la clave ya existía: no se guardó nada nuevo ni se entregó. */
  readonly created: boolean;
  /**
   * Qué pasó en cada canal por el que el aviso iba a salir, en orden. Incluye
   * los que no se intentaron por no molestar (`silenced`).
   */
  readonly deliveries: ReadonlyMap<string, DeliveryResult>;
}

export interface NotifierOptions {
  readonly policy: Policy;
  readonly catalog: Catalog;
  readonly store: NotificationStore;
  readonly preferences: PreferenceStore;
  readonly channels?: Iterable<DeliveryChannel>;
  /** Los horarios de no molestar. Sin almacén, nadie está en silencio. */
  readonly quietHours?: QuietHoursStore;
  /**
   * De una zona horaria y un instante al minuto local, o `null` si la zona no
   * se conoce. Por omisión, `localMinute`, que usa `Intl`.
   */
  readonly localMinute?: (timezone: string, moment: Date) => number | null;
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
    const targets = resolveChannels(policy, draft.category, overrides, this.#channels.keys());
    const silent = targets.length > 0 && (await this.#isQuiet(added.notification));
    for (const channelId of targets) {
      if (silent && !policy[draft.category]![channelId]!.urgent) {
        deliveries.set(channelId, { outcome: 'silenced' });
        continue;
      }
      deliveries.set(channelId, await deliver(this.#channels.get(channelId)!, added.notification));
    }
    return { notification: added.notification, created: true, deliveries };
  }

  /** Si el destinatario está en su horario de no molestar cuando se creó el aviso. */
  async #isQuiet(notification: Notification): Promise<boolean> {
    const store = this.#options.quietHours;
    if (store === undefined) return false;
    const quietHours = await store.getQuietHours(notification.recipientId);
    if (quietHours === null || !quietHours.enabled) return false;
    const minute = (this.#options.localMinute ?? localMinute)(quietHours.timezone, notification.createdAt);
    // Zona desconocida: ante la duda, se entrega.
    return minute !== null && isQuiet(quietHours, minute);
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
