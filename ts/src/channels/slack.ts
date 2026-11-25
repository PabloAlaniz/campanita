/**
 * El canal de Slack, por «incoming webhook».
 *
 * Cada persona conecta un canal de Slack y el aviso sale ahí. La conexión se
 * hace con OAuth (`slackAuthorizeUrl` y `exchangeSlackCode`): la persona elige
 * el canal en la pantalla de Slack, entre los que puede usar, y nunca ve ni
 * pega un token. Lo que queda es una URL secreta por persona; la guarda quien
 * integra, y `SlackChannel` se la pide cuando hay algo que entregar.
 *
 * https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks
 */
import type { Notification } from '../core/inbox.js';
import type { DeliveryChannel, DeliveryResult } from '../ports.js';
import { CONTROL } from './encoding.js';
import { failed } from './result.js';
import { isSlackWebhookUrl, SLACK_ERROR } from './slack-common.js';

export { isSlackWebhookUrl } from './slack-common.js';

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/** Un enlace que Slack puede mostrar: http o https, sin lo que cierra o parte la marca. */
// biome-ignore lint/suspicious/noControlCharactersInRegex: busca justamente los caracteres de control.
const LINK = /^https?:\/\/[^\u0000-\u0020\u007f<>|]+$/;

/** Lo que Slack pide escapar en un texto: `&`, `<` y `>`. */
export function escapeSlackText(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Lo que un aviso dice en Slack: el cuerpo JSON del webhook. */
export interface RenderedSlack {
  readonly text: string;
  readonly [field: string]: unknown;
}

/**
 * El mensaje por omisión, una línea por parte:
 *
 * - el título en negrita, con cada tramo de caracteres de control hecho un espacio;
 * - el cuerpo, si hay;
 * - el enlace, si es una URL http o https que Slack puede mostrar.
 *
 * El texto del aviso sale escapado: no puede armar menciones ni enlaces.
 */
export function renderSlack(notification: Notification): RenderedSlack {
  const title = escapeSlackText(notification.title.replace(CONTROL, ' ').replace(/^ +| +$/g, ''));
  const lines = [title === '' ? '' : `*${title}*`];
  if (typeof notification.body === 'string' && notification.body !== '') {
    lines.push(escapeSlackText(notification.body));
  }
  const url = notification.actionUrl;
  if (typeof url === 'string' && LINK.test(url)) lines.push(`<${url.replace(/&/g, '&amp;')}>`);
  return { text: lines.filter((line) => line !== '').join('\n') };
}

type Found = string | null | undefined | Promise<string | null | undefined>;

/**
 * De dónde sale la URL del webhook: se pasa `resolveWebhook` o
 * `resolveWebhookFor`, una de las dos. La escribe quien integra: campanita no
 * guarda conexiones.
 */
export interface SlackChannelOptions {
  /** De un destinatario a la URL de su webhook, o `null` si no conectó Slack. */
  readonly resolveWebhook?: (recipientId: string) => Found;
  /**
   * Lo mismo a partir del aviso entero, para elegir el destino según su
   * ámbito: una persona o un grupo puede tener un canal distinto en cada uno.
   */
  readonly resolveWebhookFor?: (notification: Notification) => Found;
  readonly render?: (notification: Notification) => RenderedSlack;
  /** Por omisión, `slack`. */
  readonly id?: string;
  /** En milisegundos. Por omisión, 10 000. */
  readonly timeoutMs?: number;
  /** Por omisión, el `fetch` global. */
  readonly fetch?: Fetch;
}

/**
 * Avisa en el canal de Slack que conectó cada persona.
 *
 * - Sin conexión, o con una URL que no es de Slack: `skipped`.
 * - Slack acepta el mensaje: `sent`.
 * - Slack contesta 404 o 410 (el webhook se quitó, el canal se archivó):
 *   `expired`. La conexión ya no sirve y conviene pedirle a la persona que
 *   conecte de nuevo.
 * - Cualquier otra cosa: `failed`.
 *
 * Ningún error repite la URL del webhook: es un secreto.
 */
export class SlackChannel implements DeliveryChannel {
  readonly id: string;
  readonly #options: SlackChannelOptions;
  readonly #resolve: (notification: Notification) => Found;

  constructor(options: SlackChannelOptions) {
    if ((options.resolveWebhook === undefined) === (options.resolveWebhookFor === undefined)) {
      throw new TypeError('pasá `resolveWebhook` o `resolveWebhookFor`, uno de los dos');
    }
    this.id = options.id ?? 'slack';
    this.#options = options;
    const { resolveWebhook, resolveWebhookFor } = options;
    // El constructor ya comprobó que hay una de las dos.
    this.#resolve = resolveWebhookFor ?? (resolveWebhook ? (n) => resolveWebhook(n.recipientId) : () => null);
  }

  async send(notification: Notification): Promise<DeliveryResult> {
    let url: string | null | undefined;
    try {
      url = await this.#resolve(notification);
    } catch (error) {
      return failed('no se pudo buscar la conexión de Slack', error);
    }
    if (url === null || url === undefined) {
      return { outcome: 'skipped', error: 'la persona no conectó Slack' };
    }
    if (!isSlackWebhookUrl(url)) {
      return { outcome: 'skipped', error: 'la URL guardada no es un webhook de Slack' };
    }

    let body: string;
    try {
      body = JSON.stringify((this.#options.render ?? renderSlack)(notification));
    } catch (error) {
      return failed('no se pudo armar el mensaje', error);
    }

    let response: Response;
    try {
      response = await (this.#options.fetch ?? fetch)(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        redirect: 'manual',
        signal: AbortSignal.timeout(this.#options.timeoutMs ?? 10_000),
      });
    } catch (error) {
      // Sólo el tipo: el mensaje de un error de red puede traer la URL.
      return failed('Slack', error);
    }
    if (response.ok) return { outcome: 'sent' };

    let detail = '';
    try {
      const text = (await response.text()).trim();
      if (SLACK_ERROR.test(text)) detail = ` ${text}`;
    } catch {
      // Sin detalle.
    }
    return {
      outcome: response.status === 404 || response.status === 410 ? 'expired' : 'failed',
      error: `Slack ${response.status}${detail}`,
    };
  }
}
