/**
 * El canal de push: avisa en los dispositivos de cada persona.
 *
 * Una persona tiene varios dispositivos (un teléfono, dos navegadores) y cada
 * uno se alcanza por un servicio distinto. `PushChannel` separa las dos cosas:
 * le pide los dispositivos a una función de quien integra, arma un mensaje y se
 * lo pasa, por cada dispositivo, al **transporte** que le corresponde. Un
 * transporte es un puerto más chico: `send(destino, mensaje)`.
 *
 * campanita no guarda dispositivos: registrarlos y olvidarlos es de quien
 * integra.
 */
import type { Notification } from '../core/inbox.js';
import type { DeliveryChannel, DeliveryOutcome, DeliveryResult } from '../ports.js';
import { CONTROL } from './encoding.js';
import { failed } from './result.js';


/** Lo que un aviso dice en un dispositivo. Cada transporte lo traduce a su servicio. */
export interface PushMessage {
  readonly title: string;
  readonly body: string | null;
  /** A dónde lleva tocar el aviso. Es opaca: la interpreta quien lo recibe. */
  readonly url: string | null;
  /** Los avisos con el mismo hilo se agrupan o se reemplazan, según el servicio. */
  readonly thread: string | null;
  /** Datos que viajan con el aviso, para quien lo recibe. */
  readonly data: Readonly<Record<string, string | number | boolean>>;
}

/**
 * Un dispositivo: por qué transporte se alcanza y lo que ese transporte
 * necesita para llegar (un token, una suscripción). `target` es del transporte;
 * el canal no lo mira.
 */
export interface PushDevice<Target = unknown> {
  /** La clave del transporte en `transports`. */
  readonly transport: string;
  readonly target: Target;
}

/**
 * Cualquier servicio de push. Manda UN mensaje a UN destino y contesta como un
 * canal: `sent`, `expired` (el destino ya no existe), `failed` o `skipped` (el
 * destino no tiene la forma que el servicio espera).
 *
 * No debería rechazar. Si igual rechaza, el canal lo cuenta como `failed`.
 */
export interface PushTransport<Target = unknown> {
  send(target: Target, message: PushMessage): Promise<DeliveryResult>;
}

/**
 * El mensaje por omisión:
 *
 * - el título, con cada tramo de caracteres de control hecho un espacio;
 * - el cuerpo y la dirección, si no están vacíos;
 * - sin hilo;
 * - en los datos, el identificador y el tipo del aviso, para que quien lo
 *   recibe pueda abrirlo o marcarlo como leído.
 *
 * El texto no se escapa: lo muestra el sistema, no un intérprete de marcas.
 */
export function renderPush(notification: Notification): PushMessage {
  const { body, actionUrl } = notification;
  return {
    title: notification.title.replace(CONTROL, ' ').replace(/^ +| +$/g, ''),
    body: typeof body === 'string' && body !== '' ? body : null,
    url: typeof actionUrl === 'string' && actionUrl !== '' ? actionUrl : null,
    thread: null,
    data: { notification_id: notification.id, type: notification.type },
  };
}

/**
 * De lo que pasó en cada dispositivo a lo que pasó con el aviso:
 *
 * - alguno lo recibió: `sent`;
 * - ninguno, y alguno falló: `failed` (reintentar puede servir);
 * - ninguno, y alguno venció: `expired`;
 * - no había dispositivos, o ninguno tenía transporte: `skipped`.
 */
export function summarizePush(outcomes: readonly DeliveryOutcome[]): DeliveryOutcome {
  for (const outcome of ['sent', 'failed', 'expired'] as const) {
    if (outcomes.includes(outcome)) return outcome;
  }
  return 'skipped';
}

type Maybe<T> = T | Promise<T>;

export interface PushChannelOptions {
  /**
   * Los transportes, por la clave que usa cada dispositivo en `transport`. Un
   * dispositivo cuya clave no está acá se saltea: así un servicio sin
   * configurar no impide avisar por los demás.
   */
  // Cada transporte recibe el destino que le armó quien integra: el canal no lo puede comprobar.
  // biome-ignore lint/suspicious/noExplicitAny: cada transporte tiene su propio tipo de destino.
  readonly transports: Readonly<Record<string, PushTransport<any>>>;
  /**
   * Del aviso a los dispositivos que lo tienen que recibir. La escribe quien
   * integra. Recibe el aviso entero: puede elegir según el destinatario, el
   * ámbito o la dirección a la que lleva.
   */
  readonly resolveDevices: (notification: Notification) => Maybe<readonly PushDevice[] | null | undefined>;
  /**
   * Qué pasó con cada dispositivo. Con `expired`, el dispositivo ya no existe
   * y hay que olvidarlo. Si rechaza, no cambia el resultado.
   */
  readonly onResult?: (device: PushDevice, result: DeliveryResult, notification: Notification) => Maybe<void>;
  readonly render?: (notification: Notification) => PushMessage;
  /** Por omisión, `push`. */
  readonly id?: string;
}

const OUTCOMES: ReadonlySet<unknown> = new Set(['sent', 'expired', 'failed', 'skipped']);

/**
 * Avisa en los dispositivos de cada persona.
 *
 * Cada dispositivo se resuelve por separado: uno roto no impide avisar al
 * resto. El resultado del canal es el de `summarizePush`; el de cada
 * dispositivo llega por `onResult`.
 */
export class PushChannel implements DeliveryChannel {
  readonly id: string;
  readonly #options: PushChannelOptions;

  constructor(options: PushChannelOptions) {
    if (typeof options.resolveDevices !== 'function') throw new TypeError('falta `resolveDevices`');
    if (typeof options.transports !== 'object' || options.transports === null) {
      throw new TypeError('falta `transports`');
    }
    this.id = options.id ?? 'push';
    this.#options = options;
  }

  async send(notification: Notification): Promise<DeliveryResult> {
    let devices: readonly PushDevice[];
    try {
      devices = (await this.#options.resolveDevices(notification)) ?? [];
    } catch (error) {
      return failed('no se pudieron buscar los dispositivos', error);
    }
    if (devices.length === 0) return { outcome: 'skipped', error: 'la persona no tiene dispositivos' };

    let message: PushMessage;
    try {
      message = (this.#options.render ?? renderPush)(notification);
    } catch (error) {
      return failed('no se pudo armar el mensaje', error);
    }

    const outcomes = await Promise.all(devices.map((device) => this.#deliver(device, message, notification)));
    const outcome = summarizePush(outcomes);
    if (outcome === 'sent') return { outcome };
    return { outcome, error: `push: no lo recibió ninguno de ${devices.length} dispositivos` };
  }

  async #deliver(device: PushDevice, message: PushMessage, notification: Notification): Promise<DeliveryOutcome> {
    const { transports, onResult } = this.#options;
    const transport = Object.hasOwn(transports, device.transport) ? transports[device.transport] : undefined;
    let result: DeliveryResult;
    if (transport === undefined) {
      result = { outcome: 'skipped', error: 'no hay transporte para este dispositivo' };
    } else {
      try {
        result = await transport.send(device.target, message);
        if (!OUTCOMES.has(result?.outcome)) result = { outcome: 'failed', error: 'el transporte no informó un resultado' };
      } catch (error) {
        // Sólo el tipo: el mensaje de un error de red puede traer el destino.
        result = failed('el transporte rechazó', error);
      }
    }
    try {
      await onResult?.(device, result, notification);
    } catch {
      // Anotar el resultado es de quien integra: si falla, el aviso igual salió o no salió.
    }
    return result.outcome;
  }
}
