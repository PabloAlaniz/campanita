/**
 * Conectar Slack con OAuth: la persona elige un canal y queda una URL de webhook.
 *
 * La persona elige el canal en la pantalla de Slack, entre los que puede usar, y
 * nunca ve ni pega un token. Lo que queda es una URL secreta por persona, que
 * guarda quien integra y `SlackChannel` usa para entregar.
 */
import { isSlackWebhookUrl, SLACK_ERROR } from './slack-common.js';

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

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
