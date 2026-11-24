/** El canal de push y sus transportes. Ningún test sale a la red. */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { beforeAll, describe, expect, it } from 'vitest';
import {
  ApnsTransport,
  MemoryPreferenceStore,
  MemoryStore,
  Notifier,
  parseCatalog,
  parsePolicy,
  PushChannel,
  renderPush,
  summarizePush,
  WebPushTransport,
  type ApnsTarget,
  type DeliveryOutcome,
  type DeliveryResult,
  type Notification,
  type PushDevice,
  type PushMessage,
  type PushTransport,
  type WebPushTarget,
} from '../src/index.js';

type MessageCase = {
  note: string;
  notification: { id: string; type: string; title: string; body: string | null; action_url: string | null };
  message: PushMessage;
};
type SummaryCase = { note: string; outcomes: DeliveryOutcome[]; outcome: DeliveryOutcome };
const CASES = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../fixtures/push.json', import.meta.url)), 'utf8'),
) as { mensaje: MessageCase[]; resumen: SummaryCase[] };

const notification = (changes: Partial<Notification> = {}): Notification => ({
  id: 'n1',
  recipientId: 'ana',
  type: 'nota',
  category: 'activity',
  title: 'Ana comentó',
  body: '¿Va con IVA?',
  actionUrl: 'https://example.com/doc/7',
  payload: {},
  scope: null,
  dedupeKey: null,
  createdAt: new Date('2030-01-01T00:00:00Z'),
  readAt: null,
  ...changes,
});

const MESSAGE: PushMessage = {
  title: 'Ana comentó',
  body: '¿Va con IVA?',
  url: '/doc/7',
  thread: 'doc-7',
  data: { notification_id: 'n1', count: 2, urgent: false },
};

const fakeFetch = (answer: (call: number) => Response | Promise<Response>) => {
  const calls: { url: string; init: RequestInit & { headers: Record<string, string> } }[] = [];
  return {
    calls,
    fetch: async (url: string, init: RequestInit): Promise<Response> => {
      calls.push({ url, init: init as RequestInit & { headers: Record<string, string> } });
      return answer(calls.length);
    },
  };
};

const b64url = (bytes: Uint8Array): string => Buffer.from(bytes).toString('base64url');
const unb64 = (text: string): Uint8Array<ArrayBuffer> => new Uint8Array(Buffer.from(text, 'base64url'));
const segment = (text: string): Record<string, unknown> => JSON.parse(Buffer.from(text, 'base64url').toString('utf8'));
type CryptoKey = Awaited<ReturnType<typeof crypto.subtle.importKey>>;
const ES256 = { name: 'ECDSA', namedCurve: 'P-256' } as const;

async function verifyJwt(jwt: string, publicKey: CryptoKey): Promise<boolean> {
  const [header, claims, signature] = jwt.split('.') as [string, string, string];
  return crypto.subtle.verify(
    { name: 'ECDSA', hash: 'SHA-256' },
    publicKey,
    unb64(signature),
    new TextEncoder().encode(`${header}.${claims}`),
  );
}

// --- los casos compartidos --------------------------------------------------------

describe('mensaje por omisión', () => {
  for (const test of CASES.mensaje) {
    it(test.note, () => {
      const { id, type, title, body, action_url: actionUrl } = test.notification;
      expect(renderPush(notification({ id, type, title, body, actionUrl }))).toEqual(test.message);
    });
  }
});

describe('resumen de un envío', () => {
  for (const test of CASES.resumen) {
    it(test.note, () => expect(summarizePush(test.outcomes)).toBe(test.outcome));
  }

  for (const test of CASES.resumen) {
    it(`el canal contesta lo mismo: ${test.note}`, async () => {
      const transport: PushTransport<DeliveryOutcome> = { send: async (outcome) => ({ outcome }) };
      const channel = new PushChannel({
        transports: { t: transport },
        resolveDevices: () => test.outcomes.map((outcome) => ({ transport: outcome === 'skipped' ? 'otro' : 't', target: outcome })),
      });
      expect((await channel.send(notification())).outcome).toBe(test.outcome);
    });
  }
});

// --- el canal ---------------------------------------------------------------------

describe('PushChannel', () => {
  const recorder = () => {
    const sent: { target: unknown; message: PushMessage }[] = [];
    const transport: PushTransport = {
      send: async (target, message) => {
        sent.push({ target, message });
        return { outcome: 'sent' };
      },
    };
    return { sent, transport };
  };

  it('pide los dispositivos con el aviso entero y le manda a cada uno por su transporte', async () => {
    const a = recorder();
    const b = recorder();
    const seen: Notification[] = [];
    const channel = new PushChannel({
      transports: { a: a.transport, b: b.transport },
      resolveDevices: async (n) => {
        seen.push(n);
        return [
          { transport: 'a', target: 'uno' },
          { transport: 'b', target: 'dos' },
          { transport: 'a', target: 'tres' },
        ];
      },
    });
    const aviso = notification({ scope: 'equipo-1' });
    expect(await channel.send(aviso)).toEqual({ outcome: 'sent' });
    expect(seen).toEqual([aviso]);
    expect(a.sent.map((s) => s.target)).toEqual(['uno', 'tres']);
    expect(b.sent.map((s) => s.target)).toEqual(['dos']);
    expect(a.sent[0]!.message).toEqual(renderPush(aviso));
    expect(channel.id).toBe('push');
  });

  it('sin dispositivos no hay a dónde entregar', async () => {
    const { transport, sent } = recorder();
    for (const none of [[], null, undefined]) {
      const channel = new PushChannel({ transports: { a: transport }, resolveDevices: () => none });
      expect((await channel.send(notification())).outcome).toBe('skipped');
    }
    expect(sent).toEqual([]);
  });

  it('si no se pueden buscar los dispositivos falla, y sólo dice el tipo del error', async () => {
    const channel = new PushChannel({
      transports: {},
      resolveDevices: () => {
        throw new RangeError('token-secreto');
      },
    });
    expect(await channel.send(notification())).toEqual({
      outcome: 'failed',
      error: 'no se pudieron buscar los dispositivos: RangeError',
    });
  });

  it('un transporte que rechaza o contesta cualquier cosa cuenta como fallo y no frena al resto', async () => {
    const { transport, sent } = recorder();
    const results: [PushDevice, DeliveryResult][] = [];
    const channel = new PushChannel({
      transports: {
        roto: { send: () => Promise.reject(new Error('https://example.com/secreto')) },
        raro: { send: async () => ({ outcome: 'silenced' }) },
        bien: transport,
      },
      resolveDevices: () => [
        { transport: 'roto', target: 1 },
        { transport: 'raro', target: 2 },
        { transport: 'bien', target: 3 },
      ],
      onResult: (device, result) => void results.push([device, result]),
    });
    expect((await channel.send(notification())).outcome).toBe('sent');
    expect(sent.map((s) => s.target)).toEqual([3]);
    expect(results.map(([device, result]) => [device.target, result.outcome])).toEqual([
      [1, 'failed'],
      [2, 'failed'],
      [3, 'sent'],
    ]);
    expect(JSON.stringify(results)).not.toContain('secreto');
  });

  it('`onResult` avisa de cada dispositivo, y que rechace no cambia el resultado', async () => {
    const seen: string[] = [];
    const channel = new PushChannel({
      transports: { t: { send: async (target: DeliveryOutcome) => ({ outcome: target }) } },
      resolveDevices: () => [
        { transport: 't', target: 'expired' },
        { transport: 'nadie', target: 'x' },
      ],
      onResult: async (device, result, n) => {
        seen.push(`${device.transport}:${result.outcome}:${n.id}`);
        throw new Error('la base no contesta');
      },
    });
    const result = await channel.send(notification());
    expect(result.outcome).toBe('expired');
    expect(result.error).toBe('push: no lo recibió ninguno de 2 dispositivos');
    expect(seen.sort()).toEqual(['nadie:skipped:n1', 't:expired:n1']);
  });

  it('una clave de transporte heredada no es un transporte', async () => {
    const channel = new PushChannel({ transports: {}, resolveDevices: () => [{ transport: 'toString', target: 1 }] });
    expect((await channel.send(notification())).outcome).toBe('skipped');
  });

  it('usa el `render` y el `id` que le pasen', async () => {
    const { transport, sent } = recorder();
    const channel = new PushChannel({
      id: 'movil',
      transports: { a: transport },
      resolveDevices: () => [{ transport: 'a', target: 1 }],
      render: (n) => ({ ...renderPush(n), thread: n.category }),
    });
    await channel.send(notification());
    expect(channel.id).toBe('movil');
    expect(sent[0]!.message.thread).toBe('activity');
  });

  it('un `render` que rechaza es un fallo', async () => {
    const channel = new PushChannel({
      transports: {},
      resolveDevices: () => [{ transport: 'a', target: 1 }],
      render: () => {
        throw new TypeError('x');
      },
    });
    expect(await channel.send(notification())).toEqual({ outcome: 'failed', error: 'no se pudo armar el mensaje: TypeError' });
  });

  it('exige `resolveDevices` y `transports`', () => {
    expect(() => new PushChannel({ transports: {} } as never)).toThrow(TypeError);
    expect(() => new PushChannel({ resolveDevices: () => [] } as never)).toThrow(TypeError);
  });

  it('es un canal más del recorrido', async () => {
    const { transport, sent } = recorder();
    const policy = parsePolicy({ activity: { inapp: { default: true, locked: true }, push: { default: true } } });
    const notifier = new Notifier({
      policy,
      catalog: parseCatalog({ 'comments.new': { category: 'activity', title: '{author} comentó' } }, policy),
      store: new MemoryStore(),
      preferences: new MemoryPreferenceStore(),
      channels: [new PushChannel({ transports: { a: transport }, resolveDevices: (n) => [{ transport: 'a', target: n.recipientId }] })],
    });
    const result = await notifier.notify({ type: 'comments.new', recipient_id: 'user-7', payload: { author: 'Ana' } });
    expect(result.deliveries.get('push')?.outcome).toBe('sent');
    expect(sent).toHaveLength(1);
    expect(sent[0]!.target).toBe('user-7');
    expect(sent[0]!.message.title).toBe('Ana comentó');
    expect(sent[0]!.message.data).toEqual({ notification_id: result.notification.id, type: 'comments.new' });
  });
});

// --- APNs -------------------------------------------------------------------------

describe('ApnsTransport', () => {
  let pem: string;
  let publicKey: CryptoKey;
  let serial = 0;
  const TOKEN = 'ab'.repeat(32);
  const DEVICE: ApnsTarget = { token: TOKEN, environment: 'production' };
  const accepted = (): Response => new Response('', { status: 200, headers: { 'apns-id': '0A1B2C3D-0000-4000-8000-000000000001' } });
  const refused = (status: number, reason: string): Response => new Response(JSON.stringify({ reason }), { status });

  beforeAll(async () => {
    const pair = await crypto.subtle.generateKey(ES256, true, ['sign', 'verify']);
    const der = Buffer.from(await crypto.subtle.exportKey('pkcs8', pair.privateKey)).toString('base64');
    pem = `-----BEGIN PRIVATE KEY-----\n${der.match(/.{1,64}/g)!.join('\n')}\n-----END PRIVATE KEY-----\n`;
    publicKey = pair.publicKey;
  });

  /** Cada test usa su propia clave: el «provider token» se guarda por clave, entre instancias. */
  const transport = (fake: { fetch: (url: string, init: RequestInit) => Promise<Response> }, options: Record<string, unknown> = {}) =>
    new ApnsTransport({ key: pem, keyId: `KEY${++serial}`, teamId: 'TEAM123456', topic: 'com.example.app', fetch: fake.fetch, ...options });

  it('manda al servicio de producción, con el aviso y los datos en la raíz', async () => {
    const fake = fakeFetch(accepted);
    const result = await transport(fake, { sound: 'default' }).send(DEVICE, MESSAGE);
    expect(result).toEqual({ outcome: 'sent', providerMessageId: '0A1B2C3D-0000-4000-8000-000000000001' });
    const { url, init } = fake.calls[0]!;
    expect(url).toBe(`https://api.push.apple.com/3/device/${TOKEN}`);
    expect(init.method).toBe('POST');
    expect(init.redirect).toBe('manual');
    expect(init.headers).toMatchObject({
      'apns-topic': 'com.example.app',
      'apns-push-type': 'alert',
      'apns-priority': '10',
      'content-type': 'application/json',
    });
    expect(JSON.parse(init.body as string)).toEqual({
      url: '/doc/7',
      notification_id: 'n1',
      count: 2,
      urgent: false,
      aps: { alert: { title: 'Ana comentó', body: '¿Va con IVA?' }, sound: 'default', 'thread-id': 'doc-7' },
    });
  });

  it('un token de desarrollo va al servicio de pruebas', async () => {
    const fake = fakeFetch(accepted);
    await transport(fake).send({ token: TOKEN, environment: 'sandbox' }, MESSAGE);
    expect(fake.calls[0]!.url).toBe(`https://api.sandbox.push.apple.com/3/device/${TOKEN}`);
  });

  it('sin cuerpo, sin hilo, sin dirección y sin sonido, el aviso sólo lleva el título', async () => {
    const fake = fakeFetch(accepted);
    await transport(fake).send(DEVICE, { title: 'Listo', body: null, url: null, thread: null, data: {} });
    expect(JSON.parse(fake.calls[0]!.init.body as string)).toEqual({ aps: { alert: { title: 'Listo' } } });
  });

  it('un dato llamado `aps` no pisa el aviso', async () => {
    const fake = fakeFetch(accepted);
    await transport(fake).send(DEVICE, { ...MESSAGE, data: { aps: 'x' } });
    expect(JSON.parse(fake.calls[0]!.init.body as string).aps.alert.title).toBe('Ana comentó');
  });

  it('firma un «provider token» ES256 que la clave pública verifica', async () => {
    const fake = fakeFetch(accepted);
    const t = transport(fake, { now: () => 1_900_000_000_000 });
    await t.send(DEVICE, MESSAGE);
    const jwt = fake.calls[0]!.init.headers['authorization']!.replace(/^bearer /, '');
    const [header, claims] = jwt.split('.') as [string, string];
    expect(segment(header)).toEqual({ alg: 'ES256', kid: `KEY${serial}` });
    expect(segment(claims)).toEqual({ iss: 'TEAM123456', iat: 1_900_000_000 });
    expect(await verifyJwt(jwt, publicKey)).toBe(true);
  });

  it('reusa el «provider token» 45 minutos, también entre instancias, y después firma otro', async () => {
    const fake = fakeFetch(accepted);
    let now = 1_900_000_000_000;
    const keyId = `KEY${++serial}`;
    const make = () => new ApnsTransport({ key: pem, keyId, teamId: 'TEAM123456', topic: 'com.example.app', fetch: fake.fetch, now: () => now });
    await make().send(DEVICE, MESSAGE);
    now += 44 * 60 * 1000;
    await make().send(DEVICE, MESSAGE);
    now += 2 * 60 * 1000;
    await make().send(DEVICE, MESSAGE);
    const [first, second, third] = fake.calls.map((call) => call.init.headers['authorization']);
    expect(second).toBe(first);
    expect(third).not.toBe(first);
  });

  it('si APNs dice que el «provider token» venció, firma otro y reintenta una sola vez', async () => {
    const once = fakeFetch((call) => (call === 1 ? refused(403, 'ExpiredProviderToken') : accepted()));
    expect((await transport(once).send(DEVICE, MESSAGE)).outcome).toBe('sent');
    expect(once.calls).toHaveLength(2);

    const always = fakeFetch(() => refused(403, 'InvalidProviderToken'));
    expect(await transport(always).send(DEVICE, MESSAGE)).toEqual({ outcome: 'failed', error: 'APNs 403 InvalidProviderToken' });
    expect(always.calls).toHaveLength(2);
  });

  it.each([
    [410, 'Unregistered'],
    [400, 'BadDeviceToken'],
    [400, 'DeviceTokenNotForTopic'],
    [410, 'OtraCosa'],
  ])('%i %s: el dispositivo venció', async (status, reason) => {
    const fake = fakeFetch(() => refused(status, reason));
    expect(await transport(fake).send(DEVICE, MESSAGE)).toEqual({ outcome: 'expired', error: `APNs ${status} ${reason}` });
  });

  it('cualquier otro rechazo es un fallo, y un motivo raro no se repite', async () => {
    expect(await transport(fakeFetch(() => refused(429, 'TooManyRequests'))).send(DEVICE, MESSAGE)).toEqual({
      outcome: 'failed',
      error: 'APNs 429 TooManyRequests',
    });
    expect(await transport(fakeFetch(() => refused(500, `ver ${TOKEN}`))).send(DEVICE, MESSAGE)).toEqual({
      outcome: 'failed',
      error: 'APNs 500',
    });
    expect(await transport(fakeFetch(() => new Response('<html>', { status: 502 }))).send(DEVICE, MESSAGE)).toEqual({
      outcome: 'failed',
      error: 'APNs 502',
    });
  });

  it('un error de red sólo dice su tipo', async () => {
    const fake = fakeFetch(() => Promise.reject(new TypeError(`no se pudo conectar a /3/device/${TOKEN}`)));
    expect(await transport(fake).send(DEVICE, MESSAGE)).toEqual({ outcome: 'failed', error: 'APNs: TypeError' });
  });

  it('una clave que no sirve es un fallo que no la repite', async () => {
    const fake = fakeFetch(accepted);
    const result = await transport(fake, { key: 'no-es-una-clave' }).send(DEVICE, MESSAGE);
    expect(result.outcome).toBe('failed');
    expect(result.error).not.toContain('no-es-una-clave');
    expect(fake.calls).toHaveLength(0);
  });

  it('no manda a un token que no es hexadecimal ni a un entorno desconocido', async () => {
    const fake = fakeFetch(accepted);
    const t = transport(fake);
    for (const token of ['', '../../otro', `${TOKEN}?x=1`, 'zz', 7]) {
      expect((await t.send({ token: token as string, environment: 'production' }, MESSAGE)).outcome).toBe('skipped');
    }
    expect((await t.send({ token: TOKEN, environment: 'toString' as never }, MESSAGE)).outcome).toBe('skipped');
    expect(fake.calls).toHaveLength(0);
  });

  it('exige la clave, sus identificadores y el topic', () => {
    for (const name of ['key', 'keyId', 'teamId', 'topic']) {
      expect(() => transport(fakeFetch(accepted), { [name]: '' })).toThrow(`falta \`${name}\``);
    }
  });
});

// --- Web Push ---------------------------------------------------------------------

describe('WebPushTransport', () => {
  let vapid: { publicKey: string; privateKey: string; verify: CryptoKey };
  let browser: { target: WebPushTarget; privateKey: CryptoKey; publicRaw: Uint8Array<ArrayBuffer>; auth: Uint8Array<ArrayBuffer> };
  const ENDPOINT = 'https://push.example.net/send/abc123';
  const created = (): Response => new Response(null, { status: 201 });

  beforeAll(async () => {
    const pair = await crypto.subtle.generateKey(ES256, true, ['sign', 'verify']);
    const jwk = await crypto.subtle.exportKey('jwk', pair.privateKey);
    vapid = {
      publicKey: b64url(new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey))),
      privateKey: jwk.d!,
      verify: pair.publicKey,
    };
    const ecdh = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
    const publicRaw = new Uint8Array(await crypto.subtle.exportKey('raw', ecdh.publicKey));
    const auth = crypto.getRandomValues(new Uint8Array(16));
    browser = {
      target: { endpoint: ENDPOINT, p256dh: b64url(publicRaw), auth: b64url(auth) },
      privateKey: ecdh.privateKey,
      publicRaw,
      auth,
    };
  });

  const transport = (fake: { fetch: (url: string, init: RequestInit) => Promise<Response> }, options: Record<string, unknown> = {}) =>
    new WebPushTransport({
      publicKey: vapid.publicKey,
      privateKey: vapid.privateKey,
      subject: 'mailto:avisos@example.com',
      fetch: fake.fetch,
      ...options,
    });

  /** Lo que hace el navegador al recibir: RFC 8291, escrito aparte del transporte. */
  async function decrypt(body: Uint8Array<ArrayBuffer>): Promise<string> {
    const hkdf = async (salt: Uint8Array<ArrayBuffer>, secret: Uint8Array<ArrayBuffer>, info: Uint8Array<ArrayBuffer>, length: number) => {
      const key = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveBits']);
      return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, key, length * 8));
    };
    const text = (value: string) => new Uint8Array(Buffer.from(value, 'latin1'));
    const salt = body.slice(0, 16);
    expect(new DataView(body.buffer).getUint32(16)).toBe(4096);
    const idLength = body[20]!;
    expect(idLength).toBe(65);
    const senderRaw = body.slice(21, 21 + idLength);
    const sealed = body.slice(21 + idLength);
    const senderKey = await crypto.subtle.importKey('raw', senderRaw, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
    const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: senderKey }, browser.privateKey, 256));
    const info = new Uint8Array(Buffer.concat([text('WebPush: info\0'), browser.publicRaw, senderRaw]));
    const ikm = await hkdf(browser.auth, shared, info, 32);
    const contentKey = await hkdf(salt, ikm, text('Content-Encoding: aes128gcm\0'), 16);
    const nonce = await hkdf(salt, ikm, text('Content-Encoding: nonce\0'), 12);
    const key = await crypto.subtle.importKey('raw', contentKey, 'AES-GCM', false, ['decrypt']);
    const plain = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce }, key, sealed));
    expect(plain.at(-1)).toBe(2);
    return Buffer.from(plain.subarray(0, -1)).toString('utf8');
  }

  it('manda al endpoint un mensaje que sólo el navegador puede leer', async () => {
    const fake = fakeFetch(created);
    expect(await transport(fake).send(browser.target, MESSAGE)).toEqual({ outcome: 'sent' });
    const { url, init } = fake.calls[0]!;
    expect(url).toBe(ENDPOINT);
    expect(init.method).toBe('POST');
    expect(init.redirect).toBe('manual');
    expect(init.headers).toMatchObject({ 'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', TTL: '86400' });
    const body = init.body as Uint8Array<ArrayBuffer>;
    expect(Buffer.from(body).toString('latin1')).not.toContain('IVA');
    expect(JSON.parse(await decrypt(body))).toEqual({
      title: 'Ana comentó',
      body: '¿Va con IVA?',
      url: '/doc/7',
      tag: 'doc-7',
      data: { notification_id: 'n1', count: 2, urgent: false },
    });
  });

  it('el mensaje no lleva los campos vacíos', async () => {
    const fake = fakeFetch(created);
    await transport(fake, { ttlSeconds: 60 }).send(browser.target, { title: 'Listo', body: null, url: null, thread: null, data: {} });
    expect(fake.calls[0]!.init.headers['TTL']).toBe('60');
    expect(JSON.parse(await decrypt(fake.calls[0]!.init.body as Uint8Array<ArrayBuffer>))).toEqual({ title: 'Listo' });
  });

  it('firma con VAPID para el origen del servicio', async () => {
    const fake = fakeFetch(created);
    await transport(fake, { now: () => 1_900_000_000_000 }).send(browser.target, MESSAGE);
    const match = /^vapid t=([^,]+), k=(.+)$/.exec(fake.calls[0]!.init.headers['Authorization']!)!;
    expect(match[2]).toBe(vapid.publicKey);
    const [header, claims] = match[1]!.split('.') as [string, string];
    expect(segment(header)).toEqual({ typ: 'JWT', alg: 'ES256' });
    expect(segment(claims)).toEqual({
      aud: 'https://push.example.net',
      exp: 1_900_000_000 + 12 * 60 * 60,
      sub: 'mailto:avisos@example.com',
    });
    expect(await verifyJwt(match[1]!, vapid.verify)).toBe(true);
  });

  it.each([404, 410])('%i: la suscripción venció', async (status) => {
    const fake = fakeFetch(() => new Response(ENDPOINT, { status }));
    expect(await transport(fake).send(browser.target, MESSAGE)).toEqual({ outcome: 'expired', error: `web push ${status}` });
  });

  it('otro rechazo o un error de red es un fallo que no repite el endpoint', async () => {
    expect(await transport(fakeFetch(() => new Response(ENDPOINT, { status: 429 }))).send(browser.target, MESSAGE)).toEqual({
      outcome: 'failed',
      error: 'web push 429',
    });
    const down = fakeFetch(() => Promise.reject(new TypeError(`no se pudo conectar a ${ENDPOINT}`)));
    expect(await transport(down).send(browser.target, MESSAGE)).toEqual({ outcome: 'failed', error: 'web push: TypeError' });
  });

  it('no manda a un endpoint que no es https, ni a uno que `acceptEndpoint` rechaza', async () => {
    const fake = fakeFetch(created);
    for (const endpoint of ['http://push.example.net/x', 'no es una url', 'file:///etc/passwd', 'https://u:p@push.example.net/x']) {
      expect((await transport(fake).send({ ...browser.target, endpoint }, MESSAGE)).outcome).toBe('skipped');
    }
    const strict = transport(fake, { acceptEndpoint: (url: URL) => url.hostname === 'push.example.org' });
    expect((await strict.send(browser.target, MESSAGE)).outcome).toBe('skipped');
    expect(fake.calls).toHaveLength(0);
    expect((await strict.send({ ...browser.target, endpoint: 'https://push.example.org/x' }, MESSAGE)).outcome).toBe('sent');
  });

  it('un mensaje que no entra en un aviso es un fallo, sin salir a la red', async () => {
    const fake = fakeFetch(created);
    const result = await transport(fake).send(browser.target, { ...MESSAGE, body: 'x'.repeat(4000) });
    expect(result).toEqual({ outcome: 'failed', error: 'el mensaje no entra en un aviso de web push' });
    expect(fake.calls).toHaveLength(0);
  });

  it('claves que no sirven son un fallo que no las repite', async () => {
    const fake = fakeFetch(created);
    const badSubscription = await transport(fake).send({ ...browser.target, p256dh: 'clave-rota' }, MESSAGE);
    const badVapid = await transport(fake, { publicKey: 'AAAA' }).send(browser.target, MESSAGE);
    for (const result of [badSubscription, badVapid]) {
      expect(result.outcome).toBe('failed');
      expect(result.error).toMatch(/^no se pudo armar el mensaje: \w+$/);
    }
    expect(fake.calls).toHaveLength(0);
  });

  it('exige las claves y el contacto', () => {
    for (const name of ['publicKey', 'privateKey', 'subject']) {
      expect(() => transport(fakeFetch(created), { [name]: '' })).toThrow(`falta \`${name}\``);
    }
  });
});
