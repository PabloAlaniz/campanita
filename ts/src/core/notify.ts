/** El recorrido completo: armar el aviso, guardarlo y entregarlo. */
import type {
  ChannelAvailability,
  DeliveryChannel,
  DeliveryResult,
  NotificationWriter,
  PreferenceReader,
  QuietHoursReader,
} from '../ports.js';
import { buildNotification, checkMembers, type Member } from './catalog.js';
import { resolveChannels } from './channels.js';
import type { AddResult, Notification } from './inbox.js';
import { isQuiet, isValidQuietHours, localMinute } from './quiet.js';
import { EventRejectedError, ID, INBOX, type Catalog, type Policy } from './types.js';

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
   * Las copias de un aviso de grupo, una por miembro y en su orden. `created`
   * es `false` cuando el miembro ya tenía un aviso con esa clave.
   */
  readonly copies: readonly AddResult[];
  /**
   * Qué pasó en cada canal por el que el aviso iba a salir, en orden. Incluye
   * los que no se intentaron por no molestar (`silenced`).
   */
  readonly deliveries: ReadonlyMap<string, DeliveryResult>;
}

export interface NotifierOptions {
  readonly policy: Policy;
  readonly catalog: Catalog;
  readonly store: NotificationWriter;
  readonly preferences: PreferenceReader;
  readonly channels?: Iterable<DeliveryChannel>;
  /**
   * Qué canales tiene cada destinatario: los que activó y para los que hay a
   * dónde mandar. Sin función, todos los configurados. Un canal que devuelve
   * pero no está configurado no cuenta.
   */
  readonly available?: ChannelAvailability;
  /** Los horarios de no molestar. Sin almacén, nadie está en silencio. */
  readonly quietHours?: QuietHoursReader;
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
  readonly #clock: () => Date;
  readonly #newId: () => string;

  constructor(options: NotifierOptions) {
    this.#options = options;
    this.#channels = checkChannels(options.channels ?? []);
    this.#clock = options.clock ?? (() => new Date());
    this.#newId = options.newId ?? (() => crypto.randomUUID());
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
    options: { id?: string; createdAt?: Date; members?: readonly Member[] } = {},
  ): Promise<NotifyResult> {
    const { catalog, store } = this.#options;
    const draft = buildNotification(catalog, event);
    const members = checkMembers(options.members, catalog[draft.type]?.audience === 'group');
    const createdAt = options.createdAt ?? this.#clock();
    const added = await store.add(draft, {
      id: options.id ?? this.#newId(),
      createdAt,
    });
    // El aviso ya existía: no se copia ni se vuelve a entregar. Reintentar una
    // entrega fallida es decisión de quien llama (con `deliver`).
    if (!added.created) {
      return { notification: added.notification, created: false, copies: [], deliveries: new Map() };
    }
    // Las copias van a la bandeja de cada miembro y no se entregan: el aviso
    // sale una sola vez, por los canales del grupo.
    const copies: AddResult[] = [];
    for (const member of members) {
      copies.push(await store.add({ ...draft, recipientId: member.recipient_id }, { id: member.id, createdAt }));
    }

    const deliveries = await this.#deliver(added.notification, added.notification.createdAt);
    return { notification: added.notification, created: true, copies, deliveries };
  }

  /**
   * Entrega un aviso que ya está guardado, sin guardar nada: para avisos que
   * guardó otro (un trigger de la base, otro servicio) o para reintentar una
   * entrega que falló. No mira la `dedupe_key` ni hace copias, y usa las
   * preferencias, la disponibilidad y el horario de no molestar de ahora.
   *
   * Rechaza con `EventRejectedError` (`invalid_field`, `category`) si la
   * política no tiene la categoría del aviso. Una entrega que falla nunca
   * rechaza: queda anotada en el resultado.
   */
  async deliver(notification: Notification): Promise<ReadonlyMap<string, DeliveryResult>> {
    if (!Object.hasOwn(this.#options.policy, notification.category)) {
      throw new EventRejectedError('invalid_field', 'category');
    }
    return this.#deliver(notification, this.#clock());
  }

  /** Los pasos 6 a 9 del recorrido. `moment` es el instante para el horario de no molestar. */
  async #deliver(notification: Notification, moment: Date): Promise<Map<string, DeliveryResult>> {
    const { policy, preferences } = this.#options;
    const deliveries = new Map<string, DeliveryResult>();
    const overrides = await preferences.getOverrides(notification.recipientId, { scope: notification.scope });
    const usable = await this.#usable(notification);
    // `resolveChannels` ya los devuelve ordenados por punto de código.
    const targets = resolveChannels(policy, notification.category, overrides, usable);
    const silent = targets.length > 0 && (await this.#isQuiet(notification.recipientId, moment));
    for (const channelId of targets) {
      if (silent && policy[notification.category]?.[channelId]?.urgent !== true) {
        deliveries.set(channelId, { outcome: 'silenced' });
        continue;
      }
      // `targets` sale de los canales configurados: siempre está.
      const channel = this.#channels.get(channelId);
      if (channel !== undefined) deliveries.set(channelId, await deliver(channel, notification));
    }
    return deliveries;
  }

  /** Los canales configurados que están disponibles para el destinatario del aviso. */
  async #usable(notification: Notification): Promise<string[]> {
    const available = this.#options.available;
    if (available === undefined) return [...this.#channels.keys()];
    const theirs = new Set(await available(notification.recipientId, notification.scope));
    return [...this.#channels.keys()].filter((id) => theirs.has(id));
  }

  /** Si el destinatario está en su horario de no molestar en ese instante. */
  async #isQuiet(recipientId: string, moment: Date): Promise<boolean> {
    const store = this.#options.quietHours;
    if (store === undefined) return false;
    const quietHours = await store.getQuietHours(recipientId);
    if (quietHours === null || !quietHours.enabled) return false;
    // Un horario guardado roto no silencia: el aviso ya está guardado y se entrega.
    if (!isValidQuietHours(quietHours)) return false;
    const minute = (this.#options.localMinute ?? localMinute)(quietHours.timezone, moment);
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
