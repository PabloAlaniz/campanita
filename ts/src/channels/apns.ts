/**
 * El transporte de APNs: push a dispositivos de Apple, sin intermediarios.
 *
 * Se autentica con un «provider token»: un JWT ES256 firmado con la clave .p8
 * del equipo, hecho con Web Crypto. Anda donde haya `crypto.subtle` y un
 * `fetch` que hable HTTP/2, que es lo único que APNs acepta.
 *
 * https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
 */
import type { DeliveryResult } from '../ports.js';
import { fromBase64, jsonSegment, toBase64Url, utf8 } from './encoding.js';
import type { Key } from './encoding.js';
import type { PushMessage, PushTransport } from './push.js';

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/** Un dispositivo de Apple. */
export interface ApnsTarget {
  /** El token que el sistema le dio a la app, en hexadecimal. */
  readonly token: string;
  /**
   * Contra qué servicio vale el token: `sandbox` para una app instalada desde
   * el entorno de desarrollo, `production` para una distribuida. Lo sabe la
   * app, y lo tiene que informar al registrarse.
   */
  readonly environment: 'sandbox' | 'production';
}

export interface ApnsTransportOptions {
  /** La clave .p8, con sus líneas `-----BEGIN PRIVATE KEY-----`. Es un secreto. */
  readonly key: string;
  readonly keyId: string;
  readonly teamId: string;
  /** El identificador de la app (`apns-topic`). */
  readonly topic: string;
  /** El sonido del aviso (`default` es el del sistema). Sin esto, el aviso no suena. */
  readonly sound?: string;
  /** En milisegundos. Por omisión, 10 000. */
  readonly timeoutMs?: number;
  /** Por omisión, el `fetch` global. Tiene que negociar HTTP/2. */
  readonly fetch?: Fetch;
  /** El reloj, en milisegundos. Por omisión, `Date.now`. */
  readonly now?: () => number;
}

const HOSTS = {
  sandbox: 'https://api.sandbox.push.apple.com',
  production: 'https://api.push.apple.com',
} as const;
const TOKEN = /^[0-9a-fA-F]{2,400}$/;
/** Los motivos de APNs son una palabra: sólo eso se repite en un error. */
const REASON = /^[A-Za-z]{1,60}$/;
const APNS_ID = /^[0-9A-Fa-f-]{1,64}$/;
/** El destino ya no existe, o nunca fue de esta app. */
const DEAD = new Set(['Unregistered', 'BadDeviceToken', 'DeviceTokenNotForTopic']);
/** El «provider token» venció o no sirve: se firma de nuevo y se intenta una vez más. */
const STALE = new Set(['ExpiredProviderToken', 'InvalidProviderToken']);

/**
 * APNs rechaza un «provider token» de más de una hora, y también que se firme
 * uno nuevo muy seguido. Se firma cada 45 minutos y se guarda por clave, fuera
 * de la instancia: donde el transporte se arma en cada pedido, el token
 * sobrevive entre pedidos.
 */
const TOKEN_TTL_MS = 45 * 60 * 1000;
const providerTokens = new Map<string, { readonly jwt: string; readonly signedAt: number }>();

async function reasonOf(response: Response): Promise<string | null> {
  try {
    const reason = ((await response.json()) as { reason?: unknown } | null)?.reason;
    return typeof reason === 'string' && REASON.test(reason) ? reason : null;
  } catch {
    return null;
  }
}

/**
 * Manda a un dispositivo de Apple.
 *
 * - El token no es hexadecimal, o el entorno no es uno de los dos: `skipped`.
 * - APNs acepta el aviso: `sent`, con su `apns-id`.
 * - APNs dice que el token ya no existe o no es de esta app: `expired`. Un
 *   token usado contra el entorno equivocado también cae acá.
 * - Cualquier otra cosa: `failed`.
 *
 * En el cuerpo, los datos del mensaje van en la raíz, junto a `aps`; la
 * dirección va en `url` y el hilo en `thread-id`. Ningún error repite el
 * token ni la clave.
 */
export class ApnsTransport implements PushTransport<ApnsTarget> {
  readonly #options: ApnsTransportOptions;
  #key: Promise<Key> | null = null;

  constructor(options: ApnsTransportOptions) {
    for (const name of ['key', 'keyId', 'teamId', 'topic'] as const) {
      if (typeof options[name] !== 'string' || options[name] === '') throw new TypeError(`falta \`${name}\``);
    }
    this.#options = options;
  }

  async send(target: ApnsTarget, message: PushMessage): Promise<DeliveryResult> {
    if (typeof target?.token !== 'string' || !TOKEN.test(target.token)) {
      return { outcome: 'skipped', error: 'el token guardado no es un token de APNs' };
    }
    if (!Object.hasOwn(HOSTS, target.environment)) {
      return { outcome: 'skipped', error: 'el entorno guardado no es `sandbox` ni `production`' };
    }

    let body: string;
    try {
      body = JSON.stringify(this.#payload(message));
    } catch (error) {
      return { outcome: 'failed', error: `no se pudo armar el mensaje: ${error instanceof Error ? error.name : 'Error'}` };
    }

    try {
      let response = await this.#post(target, body, await this.#providerToken(false));
      let reason = response.ok ? null : await reasonOf(response);
      if (reason !== null && STALE.has(reason)) {
        response = await this.#post(target, body, await this.#providerToken(true));
        reason = response.ok ? null : await reasonOf(response);
      }
      if (response.ok) {
        const id = response.headers.get('apns-id');
        return { outcome: 'sent', providerMessageId: id !== null && APNS_ID.test(id) ? id : null };
      }
      return {
        outcome: response.status === 410 || (reason !== null && DEAD.has(reason)) ? 'expired' : 'failed',
        error: `APNs ${response.status}${reason === null ? '' : ` ${reason}`}`,
      };
    } catch (error) {
      // Sólo el tipo: el mensaje de un error de red puede traer el token.
      return { outcome: 'failed', error: `APNs: ${error instanceof Error ? error.name : 'Error'}` };
    }
  }

  #payload(message: PushMessage): Record<string, unknown> {
    const aps: Record<string, unknown> = {
      alert: message.body === null ? { title: message.title } : { title: message.title, body: message.body },
    };
    if (this.#options.sound !== undefined) aps['sound'] = this.#options.sound;
    if (message.thread !== null) aps['thread-id'] = message.thread;
    // `aps` va al final: un dato con ese nombre no lo pisa.
    return { ...(message.url === null ? {} : { url: message.url }), ...message.data, aps };
  }

  #post(target: ApnsTarget, body: string, jwt: string): Promise<Response> {
    return (this.#options.fetch ?? fetch)(`${HOSTS[target.environment]}/3/device/${target.token}`, {
      method: 'POST',
      headers: {
        authorization: `bearer ${jwt}`,
        'apns-topic': this.#options.topic,
        'apns-push-type': 'alert',
        'apns-priority': '10',
        'content-type': 'application/json',
      },
      body,
      redirect: 'manual',
      signal: AbortSignal.timeout(this.#options.timeoutMs ?? 10_000),
    });
  }

  async #providerToken(renew: boolean): Promise<string> {
    const { keyId, teamId } = this.#options;
    const now = (this.#options.now ?? Date.now)();
    const cacheKey = `${teamId}/${keyId}`;
    const cached = providerTokens.get(cacheKey);
    if (!renew && cached !== undefined && now - cached.signedAt < TOKEN_TTL_MS) return cached.jwt;

    const unsigned = `${jsonSegment({ alg: 'ES256', kid: keyId })}.${jsonSegment({ iss: teamId, iat: Math.floor(now / 1000) })}`;
    // ECDSA de Web Crypto ya devuelve la firma como la pide un JWT: r y s, 64 bytes.
    const signature = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, await this.#importKey(), utf8(unsigned));
    const jwt = `${unsigned}.${toBase64Url(new Uint8Array(signature))}`;
    providerTokens.set(cacheKey, { jwt, signedAt: now });
    return jwt;
  }

  #importKey(): Promise<Key> {
    this.#key ??= crypto.subtle.importKey(
      'pkcs8',
      fromBase64(this.#options.key.replace(/-----[A-Z ]+-----/g, '').replace(/\s+/g, '')),
      { name: 'ECDSA', namedCurve: 'P-256' },
      false,
      ['sign'],
    );
    return this.#key;
  }
}
