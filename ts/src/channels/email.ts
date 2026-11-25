/**
 * El canal de email.
 *
 * Una cosa es avisar por email y otra es con qué se manda. `EmailChannel` busca
 * la dirección de la persona y arma el mensaje; el envío lo hace un
 * `EmailTransport` (un servicio por HTTP…), que se cambia sin tocar el resto.
 */
import type { Notification } from '../core/inbox.js';
import type { DeliveryChannel, DeliveryResult } from '../ports.js';

// biome-ignore lint/suspicious/noControlCharactersInRegex: busca justamente los caracteres de control.
const CONTROL = /[\u0000-\u001f\u007f]+/g;
// biome-ignore lint/suspicious/noControlCharactersInRegex: busca justamente los caracteres de control.
const HAS_CONTROL = /[\u0000-\u001f\u007f]/;
/** Sólo ASCII visible, sin los caracteres que arman una lista o un nombre. */
const ADDRESS_CHARS = /^[!-~]+$/;
const FORBIDDEN = /[<>,;:"()[\]\\]/;
const MAX_ADDRESS = 254;

/**
 * La regla del spec. Es estricta a propósito: una dirección que pasa no puede
 * partir una cabecera ni esconder un segundo destinatario.
 */
export function isValidAddress(address: unknown): address is string {
  if (typeof address !== 'string' || address.length < 3 || address.length > MAX_ADDRESS) return false;
  if (!ADDRESS_CHARS.test(address) || FORBIDDEN.test(address)) return false;
  const at = address.indexOf('@');
  return at > 0 && at === address.lastIndexOf('@') && at < address.length - 1;
}

/** Lo que un aviso dice por email. */
export interface RenderedEmail {
  readonly subject: string;
  readonly text: string;
  readonly html?: string | null;
}

/**
 * El mensaje por omisión, el del spec.
 *
 * - asunto: el título, con cada tramo de caracteres de control hecho un espacio;
 * - texto: el cuerpo y el enlace, con lo que haya; si no hay ninguno, el título.
 */
export function renderEmail(notification: Notification): RenderedEmail {
  const parts = [notification.body, notification.actionUrl].filter(
    (part): part is string => typeof part === 'string' && part !== '',
  );
  return {
    subject: notification.title.replace(CONTROL, ' '),
    text: parts.length > 0 ? parts.join('\n\n') : notification.title,
  };
}

/** Un mensaje listo para mandar. Es lo que recibe un transporte. */
export interface EmailMessage {
  readonly to: string;
  readonly sender: string;
  readonly subject: string;
  readonly text: string;
  readonly html: string | null;
  /** El nombre que acompaña a la dirección del remitente. */
  readonly senderName: string | null;
  /**
   * El identificador del aviso. Un transporte lo puede usar para que mandar dos
   * veces el mismo mensaje no llegue dos veces.
   */
  readonly idempotencyKey: string | null;
}

/** `Nombre <dirección>`, o la dirección sola. */
export function formatSender(message: Pick<EmailMessage, 'sender' | 'senderName'>): string {
  return message.senderName === null ? message.sender : `${message.senderName} <${message.sender}>`;
}

/**
 * Con qué se manda un mensaje.
 *
 * `send` no debería rechazar: informa `sent` o `failed`. Si igual rechaza,
 * `EmailChannel` lo anota como `failed`.
 */
export interface EmailTransport {
  send(message: EmailMessage): Promise<DeliveryResult>;
}

type Found = string | null | undefined | Promise<string | null | undefined>;

export interface EmailChannelOptions {
  readonly transport: EmailTransport;
  readonly sender: string;
  /**
   * De un destinatario a su dirección, o `null` si no tiene. Se pasa ésta o
   * `resolveAddressFor`, una de las dos. La escribe quien integra: campanita
   * no guarda direcciones.
   */
  readonly resolveAddress?: (recipientId: string) => Found;
  /** Lo mismo a partir del aviso entero, para elegir la dirección según su ámbito. */
  readonly resolveAddressFor?: (notification: Notification) => Found;
  readonly senderName?: string | null;
  readonly render?: (notification: Notification) => RenderedEmail;
  /** Por omisión, `email`. */
  readonly id?: string;
}

/** Avisa por email. */
export class EmailChannel implements DeliveryChannel {
  readonly id: string;
  readonly #options: EmailChannelOptions;
  readonly #senderName: string | null;

  constructor(options: EmailChannelOptions) {
    if ((options.resolveAddress === undefined) === (options.resolveAddressFor === undefined)) {
      throw new TypeError('pasá `resolveAddress` o `resolveAddressFor`, uno de los dos');
    }
    if (!isValidAddress(options.sender)) {
      throw new TypeError('`sender` no es una dirección válida');
    }
    const name = options.senderName ?? null;
    if (name !== null && (name.trim() === '' || HAS_CONTROL.test(name) || FORBIDDEN.test(name))) {
      throw new TypeError(
        '`senderName` no puede estar vacío ni tener caracteres de control o de dirección',
      );
    }
    this.id = options.id ?? 'email';
    this.#options = options;
    this.#senderName = name;
  }

  async send(notification: Notification): Promise<DeliveryResult> {
    let address: string | null | undefined;
    try {
      const { resolveAddress, resolveAddressFor } = this.#options;
      address = await (resolveAddressFor ? resolveAddressFor(notification) : resolveAddress!(notification.recipientId));
    } catch (error) {
      const kind = error instanceof Error ? error.name : 'Error';
      return { outcome: 'failed', error: `no se pudo buscar la dirección: ${kind}` };
    }
    if (address === null || address === undefined) {
      return { outcome: 'skipped', error: 'la persona no tiene dirección de email' };
    }
    if (!isValidAddress(address)) {
      return { outcome: 'skipped', error: 'la dirección de email no es válida' };
    }

    try {
      const rendered = (this.#options.render ?? renderEmail)(notification);
      return await this.#options.transport.send({
        to: address,
        sender: this.#options.sender,
        senderName: this.#senderName,
        // El asunto va a una cabecera: nunca sale con saltos de línea, tampoco
        // si lo armó una función propia.
        subject: rendered.subject.replace(CONTROL, ' '),
        text: rendered.text,
        html: rendered.html ?? null,
        idempotencyKey: notification.id,
      });
    } catch (error) {
      const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      return { outcome: 'failed', error: text };
    }
  }
}
