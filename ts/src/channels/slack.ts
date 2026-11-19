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

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

const WEBHOOK = /^https:\/\/hooks\.slack\.com\/services\/[A-Za-z0-9/_-]{1,200}$/;
const CONTROL = /[\u0000-\u001f\u007f]+/g;
/** Un enlace que Slack puede mostrar: http o https, sin lo que cierra o parte la marca. */
const LINK = /^https?:\/\/[^\u0000-\u0020\u007f<>|]+$/;
/** Los errores de Slack son una palabra en minúsculas: sólo eso se repite. */
const SLACK_ERROR = /^[a-z0-9_]{1,60}$/;

/**
 * ¿Es la URL de un webhook de Slack?
 *
 * Estricta a propósito: sólo `https://hooks.slack.com/services/…`, sin
 * usuario, puerto, consulta ni fragmento. Así una URL guardada no sirve para
 * hacer pedidos a otro servidor.
 */
export function isSlackWebhookUrl(url: unknown): url is string {
  return typeof url === 'string' && WEBHOOK.test(url);
}

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

  constructor(options: SlackChannelOptions) {
    if ((options.resolveWebhook === undefined) === (options.resolveWebhookFor === undefined)) {
      throw new TypeError('pasá `resolveWebhook` o `resolveWebhookFor`, uno de los dos');
    }
    this.id = options.id ?? 'slack';
    this.#options = options;
  }

  async send(notification: Notification): Promise<DeliveryResult> {
    let url: string | null | undefined;
    try {
      const { resolveWebhook, resolveWebhookFor } = this.#options;
      url = await (resolveWebhookFor ? resolveWebhookFor(notification) : resolveWebhook!(notification.recipientId));
    } catch (error) {
      const kind = error instanceof Error ? error.name : 'Error';
      return { outcome: 'failed', error: `no se pudo buscar la conexión de Slack: ${kind}` };
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
      return { outcome: 'failed', error: `no se pudo armar el mensaje: ${error instanceof Error ? error.name : 'Error'}` };
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
      return { outcome: 'failed', error: `Slack: ${error instanceof Error ? error.name : 'Error'}` };
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

// --- conectar con OAuth ---------------------------------------------------------

/** El único permiso que hace falta: publicar en el canal que la persona elija. */
const SCOPE = 'incoming-webhook';

/**
 * La URL de Slack a la que se manda a la persona para que elija un canal.
 *
 * `state` es un valor de quien integra, atado a la sesión de la persona e
 * imposible de adivinar: al volver hay que comprobar que es el mismo. Sin eso,
 * otra persona podría conectar su canal a una cuenta ajena.
 */
export function slackAuthorizeUrl(options: {
  readonly clientId: string;
  readonly redirectUri: string;
  readonly state: string;
}): string {
  for (const [name, value] of Object.entries(options)) {
    if (typeof value !== 'string' || value === '') throw new TypeError(`falta \`${name}\``);
  }
  const query = new URLSearchParams({
    client_id: options.clientId,
    scope: SCOPE,
    redirect_uri: options.redirectUri,
    state: options.state,
  });
  return `https://slack.com/oauth/v2/authorize?${query.toString()}`;
}

/** Lo que queda de una conexión. `webhookUrl` es un secreto. */
export interface SlackConnection {
  readonly webhookUrl: string;
  /** El nombre del canal elegido, como lo muestra Slack (`#general`). */
  readonly channel: string | null;
  readonly channelId: string | null;
  readonly teamId: string | null;
  readonly teamName: string | null;
  /** Dónde la persona administra o quita el webhook en Slack. */
  readonly configurationUrl: string | null;
}

/**
 * No se pudo terminar la conexión.
 *
 * `reason` es el error que dio Slack (`invalid_code`, `access_denied`…) o uno
 * propio: `network`, `http`, `invalid_response` o `no_webhook`.
 */
export class SlackOAuthError extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(`no se pudo conectar con Slack: ${reason}`);
    this.name = 'SlackOAuthError';
    this.reason = reason;
  }
}

const text = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null);
const field = (value: unknown, key: string): unknown =>
  typeof value === 'object' && value !== null ? (value as Record<string, unknown>)[key] : undefined;

/** De la respuesta de `oauth.v2.access` a una conexión. Rechaza con `SlackOAuthError`. */
export function parseSlackOAuthResponse(response: unknown): SlackConnection {
  if (typeof response !== 'object' || response === null) throw new SlackOAuthError('invalid_response');
  if (field(response, 'ok') !== true) {
    const reason = field(response, 'error');
    throw new SlackOAuthError(typeof reason === 'string' && SLACK_ERROR.test(reason) ? reason : 'invalid_response');
  }
  const webhook = field(response, 'incoming_webhook');
  const url = field(webhook, 'url');
  if (!isSlackWebhookUrl(url)) throw new SlackOAuthError('no_webhook');
  const team = field(response, 'team');
  return {
    webhookUrl: url,
    channel: text(field(webhook, 'channel')),
    channelId: text(field(webhook, 'channel_id')),
    teamId: text(field(team, 'id')),
    teamName: text(field(team, 'name')),
    configurationUrl: text(field(webhook, 'configuration_url')),
  };
}

/**
 * Cambia el `code` con el que Slack devuelve a la persona por su conexión.
 *
 * `redirectUri` tiene que ser el mismo que se usó en `slackAuthorizeUrl`.
 * Rechaza con `SlackOAuthError`; ningún error repite el secreto ni el código.
 */
export async function exchangeSlackCode(options: {
  readonly clientId: string;
  readonly clientSecret: string;
  readonly code: string;
  readonly redirectUri: string;
  readonly timeoutMs?: number;
  readonly fetch?: Fetch;
}): Promise<SlackConnection> {
  const { clientId, clientSecret, code, redirectUri } = options;
  for (const [name, value] of Object.entries({ clientId, clientSecret, code, redirectUri })) {
    if (typeof value !== 'string' || value === '') throw new TypeError(`falta \`${name}\``);
  }
  let response: Response;
  try {
    response = await (options.fetch ?? fetch)('https://slack.com/api/oauth.v2.access', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        code,
        redirect_uri: redirectUri,
      }).toString(),
      redirect: 'manual',
      signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
    });
  } catch {
    throw new SlackOAuthError('network');
  }
  if (!response.ok) throw new SlackOAuthError('http');
  let json: unknown;
  try {
    json = await response.json();
  } catch {
    throw new SlackOAuthError('invalid_response');
  }
  return parseSlackOAuthResponse(json);
}
