/** Transportes de email por HTTP, con `fetch`. */
import type { DeliveryResult } from '../ports.js';
import { failed } from './result.js';
import { formatSender, type EmailMessage, type EmailTransport } from './email.js';

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/** `{from, to, subject, text}` y `html` si hay. */
export function defaultBody(message: EmailMessage): Record<string, unknown> {
  const body: Record<string, unknown> = {
    from: formatSender(message),
    to: message.to,
    subject: message.subject,
    text: message.text,
  };
  if (message.html !== null) body['html'] = message.html;
  return body;
}

export interface HttpEmailTransportOptions {
  /** Las cabeceras de todos los pedidos. Ahí van las credenciales. */
  readonly headers?: Readonly<Record<string, string>>;
  /** El JSON a mandar. Por omisión, `defaultBody`. */
  readonly buildBody?: (message: EmailMessage) => unknown;
  /** Cabeceras que dependen del mensaje. */
  readonly buildHeaders?: (message: EmailMessage) => Readonly<Record<string, string>>;
  /** De la respuesta JSON al identificador del mensaje. */
  readonly readId?: (response: unknown) => unknown;
  /**
   * De la respuesta JSON de un 2xx al resultado de la entrega. Para servicios
   * que contestan 2xx también cuando no mandaron el mensaje y lo dicen en el
   * cuerpo. Sólo vale `sent`, `failed` o `skipped`; un cuerpo que no es JSON,
   * una función que lanza o cualquier otro resultado es `failed`. Si está,
   * `readId` no se usa.
   */
  readonly readResult?: (response: unknown) => DeliveryResult;
  /** En milisegundos. Por omisión, 10 000. */
  readonly timeoutMs?: number;
  /** Por omisión, el `fetch` global. */
  readonly fetch?: Fetch;
}

/**
 * Manda el mensaje como JSON por `POST` a un servicio de email.
 *
 * Sirve para cualquier servicio con una API HTTP: se le dice la URL, las
 * cabeceras y, si el servicio espera otra forma, cómo armar el cuerpo.
 *
 * Una respuesta 2xx es `sent`; cualquier otra cosa, `failed`. Si el servicio
 * contesta 2xx también cuando no mandó el mensaje, `readResult` lee el
 * resultado del cuerpo.
 */
export class HttpEmailTransport implements EmailTransport {
  readonly #url: string;
  readonly #options: HttpEmailTransportOptions;

  constructor(url: string, options: HttpEmailTransportOptions = {}) {
    let parsed: URL | null = null;
    try {
      parsed = new URL(url);
    } catch {
      // Se informa abajo.
    }
    if (parsed === null || (parsed.protocol !== 'https:' && parsed.protocol !== 'http:')) {
      throw new TypeError('`url` tiene que ser una URL http o https');
    }
    this.#url = url;
    this.#options = options;
  }

  async send(message: EmailMessage): Promise<DeliveryResult> {
    const { headers, buildBody, buildHeaders, readId, readResult, timeoutMs } = this.#options;
    let response: Response;
    try {
      response = await (this.#options.fetch ?? fetch)(this.#url, {
        method: 'POST',
        headers: { ...headers, ...buildHeaders?.(message), 'Content-Type': 'application/json' },
        body: JSON.stringify((buildBody ?? defaultBody)(message)),
        // Una redirección no se sigue: seguirla reenviaría las cabeceras, con
        // sus credenciales, a otro servidor.
        redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs ?? 10_000),
      });
    } catch (error) {
      // Sólo el tipo: el mensaje de un error de red no lo controlamos.
      return failed('HTTP', error);
    }
    if (!response.ok) {
      // Sólo el código: el cuerpo del error es del servicio y puede repetir datos del pedido.
      return { outcome: 'failed', error: `HTTP ${response.status}` };
    }
    if (readResult !== undefined) return interpret(response, readResult);
    if (readId === undefined) return { outcome: 'sent' };
    let id: unknown;
    try {
      id = readId(await response.json());
    } catch {
      id = null;
    }
    return typeof id === 'string' ? { outcome: 'sent', providerMessageId: id } : { outcome: 'sent' };
  }
}

const READABLE_OUTCOMES: ReadonlySet<string> = new Set(['sent', 'failed', 'skipped']);

/** El resultado que dice el cuerpo, sin repetir el cuerpo si no se entiende. */
async function interpret(
  response: Response,
  readResult: (response: unknown) => DeliveryResult,
): Promise<DeliveryResult> {
  let result: DeliveryResult;
  try {
    result = readResult(await response.json());
  } catch {
    return { outcome: 'failed', error: 'respuesta ilegible' };
  }
  if (typeof result !== 'object' || result === null || !READABLE_OUTCOMES.has(result.outcome)) {
    return { outcome: 'failed', error: 'respuesta ilegible' };
  }
  return result;
}

/**
 * Manda por la API de Resend (https://resend.com/docs/api-reference/emails/send-email).
 *
 * Usa el identificador del aviso como `Idempotency-Key`: reintentar un envío no
 * hace llegar el mensaje dos veces.
 */
export class ResendTransport extends HttpEmailTransport {
  constructor(apiKey: string, options: { baseUrl?: string; timeoutMs?: number; fetch?: Fetch } = {}) {
    if (apiKey === '') throw new TypeError('falta `apiKey`');
    super(`${(options.baseUrl ?? 'https://api.resend.com').replace(/\/+$/, '')}/emails`, {
      headers: { Authorization: `Bearer ${apiKey}` },
      buildBody: (message) => ({ ...defaultBody(message), to: [message.to] }),
      buildHeaders: (message): Record<string, string> =>
        message.idempotencyKey === null ? {} : { 'Idempotency-Key': message.idempotencyKey },
      readId: (response) =>
        typeof response === 'object' && response !== null ? (response as { id?: unknown }).id : null,
      ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
      ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
    });
  }
}
