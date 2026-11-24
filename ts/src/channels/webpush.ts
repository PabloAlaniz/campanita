/**
 * El transporte de Web Push: avisa en un navegador suscripto.
 *
 * Firma con VAPID (RFC 8292) y cifra el mensaje para el navegador (RFC 8291,
 * `aes128gcm`), todo con Web Crypto. Anda donde haya `crypto.subtle` y
 * `fetch`.
 *
 * El mensaje viaja como JSON: `title`, `body`, `url`, `tag` (el hilo) y `data`,
 * sin los campos vacíos. Mostrarlo es del service worker de quien integra.
 */
import type { DeliveryResult } from '../ports.js';
import { concat, fromBase64, jsonSegment, toBase64Url, utf8 } from './encoding.js';
import type { Key } from './encoding.js';
import type { PushMessage, PushTransport } from './push.js';

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/** La suscripción de un navegador, como la entrega `PushSubscription.toJSON()`. */
export interface WebPushTarget {
  readonly endpoint: string;
  /** La clave pública del navegador, en base64 de URL. */
  readonly p256dh: string;
  /** El secreto de la suscripción, en base64 de URL. */
  readonly auth: string;
}

export interface WebPushTransportOptions {
  /** La clave pública VAPID: un punto P-256 sin comprimir (65 bytes), en base64 de URL. */
  readonly publicKey: string;
  /** La clave privada VAPID (32 bytes), en base64 de URL. Es un secreto. */
  readonly privateKey: string;
  /** Cómo contactar a quien manda: `mailto:…` o una URL `https:`. */
  readonly subject: string;
  /** Cuánto guarda el servicio un aviso que no pudo entregar, en segundos. Por omisión, un día. */
  readonly ttlSeconds?: number;
  /**
   * ¿Se le manda a este `endpoint`? El endpoint lo informa el navegador, es
   * decir quien usa la app: por omisión se acepta cualquier URL `https:`. Para
   * limitar a los servicios conocidos, contestá `false` para el resto.
   */
  readonly acceptEndpoint?: (endpoint: URL) => boolean;
  /** En milisegundos. Por omisión, 10 000. */
  readonly timeoutMs?: number;
  /** Por omisión, el `fetch` global. */
  readonly fetch?: Fetch;
  /** El reloj, en milisegundos. Por omisión, `Date.now`. */
  readonly now?: () => number;
}

/** Lo máximo que un servicio de push está obligado a aceptar (RFC 8030). */
const MAX_BODY = 4096;
const P256 = { name: 'ECDH', namedCurve: 'P-256' } as const;

async function hkdf(salt: Uint8Array<ArrayBuffer>, secret: Uint8Array<ArrayBuffer>, info: Uint8Array<ArrayBuffer>, length: number) {
  const key = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, key, length * 8));
}

/**
 * Cifra `plaintext` para una suscripción (RFC 8291) y arma el cuerpo
 * `aes128gcm` de un solo registro (RFC 8188): sal, tamaño de registro, la
 * clave pública de este envío y el texto cifrado.
 */
async function encryptWebPush(
  plaintext: Uint8Array,
  keys: { readonly p256dh: string; readonly auth: string },
): Promise<Uint8Array<ArrayBuffer>> {
  const receiverPublic = fromBase64(keys.p256dh);
  const authSecret = fromBase64(keys.auth);
  const receiverKey = await crypto.subtle.importKey('raw', receiverPublic, P256, false, []);
  const sender = (await crypto.subtle.generateKey(P256, true, ['deriveBits'])) as { publicKey: Key; privateKey: Key };
  const senderPublic = new Uint8Array((await crypto.subtle.exportKey('raw', sender.publicKey)) as ArrayBuffer);
  const shared = new Uint8Array(
    await crypto.subtle.deriveBits({ name: 'ECDH', public: receiverKey }, sender.privateKey, 256),
  );

  const salt = crypto.getRandomValues(new Uint8Array(16));
  const ikm = await hkdf(authSecret, shared, concat(utf8('WebPush: info\0'), receiverPublic, senderPublic), 32);
  const contentKey = await hkdf(salt, ikm, utf8('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, utf8('Content-Encoding: nonce\0'), 12);

  const key = await crypto.subtle.importKey('raw', contentKey, 'AES-GCM', false, ['encrypt']);
  // El 0x02 final marca el último registro.
  const sealed = new Uint8Array(
    await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, key, concat(plaintext, Uint8Array.of(2))),
  );
  const recordSize = new Uint8Array(4);
  new DataView(recordSize.buffer).setUint32(0, MAX_BODY, false);
  return concat(salt, recordSize, Uint8Array.of(senderPublic.length), senderPublic, sealed);
}

/**
 * Manda a un navegador suscripto.
 *
 * - El `endpoint` no es una URL `https:`, o `acceptEndpoint` lo rechaza:
 *   `skipped`.
 * - El servicio acepta el aviso: `sent`.
 * - El servicio contesta 404 o 410 (la suscripción ya no existe): `expired`.
 * - Cualquier otra cosa, incluido un mensaje que no entra en lo que un
 *   servicio acepta: `failed`.
 *
 * No sigue redirecciones, y ningún error repite el endpoint ni las claves.
 */
export class WebPushTransport implements PushTransport<WebPushTarget> {
  readonly #options: WebPushTransportOptions;
  #key: Promise<Key> | null = null;

  constructor(options: WebPushTransportOptions) {
    for (const name of ['publicKey', 'privateKey', 'subject'] as const) {
      if (typeof options[name] !== 'string' || options[name] === '') throw new TypeError(`falta \`${name}\``);
    }
    this.#options = options;
  }

  async send(target: WebPushTarget, message: PushMessage): Promise<DeliveryResult> {
    let endpoint: URL;
    try {
      endpoint = new URL(target.endpoint);
    } catch {
      return { outcome: 'skipped', error: 'el endpoint guardado no es una URL' };
    }
    if (endpoint.protocol !== 'https:' || endpoint.username !== '' || endpoint.password !== '') {
      return { outcome: 'skipped', error: 'el endpoint guardado no es una URL https' };
    }
    if (this.#options.acceptEndpoint !== undefined && !this.#options.acceptEndpoint(endpoint)) {
      return { outcome: 'skipped', error: 'el endpoint guardado no es de un servicio aceptado' };
    }

    let body: Uint8Array<ArrayBuffer>;
    let authorization: string;
    try {
      body = await encryptWebPush(utf8(JSON.stringify(payloadOf(message))), target);
      if (body.length > MAX_BODY) return { outcome: 'failed', error: 'el mensaje no entra en un aviso de web push' };
      authorization = `vapid t=${await this.#sign(endpoint.origin)}, k=${this.#options.publicKey}`;
    } catch (error) {
      return { outcome: 'failed', error: `no se pudo armar el mensaje: ${error instanceof Error ? error.name : 'Error'}` };
    }

    let response: Response;
    try {
      response = await (this.#options.fetch ?? fetch)(endpoint.href, {
        method: 'POST',
        headers: {
          Authorization: authorization,
          'Content-Encoding': 'aes128gcm',
          'Content-Type': 'application/octet-stream',
          TTL: String(this.#options.ttlSeconds ?? 86_400),
        },
        body,
        redirect: 'manual',
        signal: AbortSignal.timeout(this.#options.timeoutMs ?? 10_000),
      });
    } catch (error) {
      // Sólo el tipo: el mensaje de un error de red puede traer el endpoint.
      return { outcome: 'failed', error: `web push: ${error instanceof Error ? error.name : 'Error'}` };
    }
    if (response.ok) return { outcome: 'sent' };
    return {
      outcome: response.status === 404 || response.status === 410 ? 'expired' : 'failed',
      error: `web push ${response.status}`,
    };
  }

  /** El JWT de VAPID para un servicio (`aud` es su origen). Vale 12 horas; el máximo es 24. */
  async #sign(audience: string): Promise<string> {
    const exp = Math.floor((this.#options.now ?? Date.now)() / 1000) + 12 * 60 * 60;
    const unsigned = `${jsonSegment({ typ: 'JWT', alg: 'ES256' })}.${jsonSegment({ aud: audience, exp, sub: this.#options.subject })}`;
    const signature = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, await this.#importKey(), utf8(unsigned));
    return `${unsigned}.${toBase64Url(new Uint8Array(signature))}`;
  }

  #importKey(): Promise<Key> {
    this.#key ??= (async () => {
      const point = fromBase64(this.#options.publicKey);
      if (point.length !== 65 || point[0] !== 4) throw new TypeError('la clave pública VAPID no es un punto P-256 sin comprimir');
      const jwk = {
        kty: 'EC',
        crv: 'P-256',
        x: toBase64Url(point.slice(1, 33)),
        y: toBase64Url(point.slice(33)),
        d: toBase64Url(fromBase64(this.#options.privateKey)),
      };
      return crypto.subtle.importKey('jwk', jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
    })();
    return this.#key;
  }
}

function payloadOf(message: PushMessage): Record<string, unknown> {
  const payload: Record<string, unknown> = { title: message.title };
  if (message.body !== null) payload['body'] = message.body;
  if (message.url !== null) payload['url'] = message.url;
  if (message.thread !== null) payload['tag'] = message.thread;
  if (Object.keys(message.data).length > 0) payload['data'] = message.data;
  return payload;
}
