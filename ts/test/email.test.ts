/** El canal de email y sus transportes. Ningún test sale a la red. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  EmailChannel,
  HttpEmailTransport,
  isValidAddress,
  MemoryPreferenceStore,
  MemoryStore,
  Notifier,
  parseCatalog,
  parsePolicy,
  renderEmail,
  ResendTransport,
  type DeliveryResult,
  type EmailMessage,
  type EmailTransport,
  type Notification,
} from '../src/index.js';

const VECTORS = fileURLToPath(new URL('../../spec/vectors/email', import.meta.url));
const load = <T>(name: string): T[] =>
  (JSON.parse(readFileSync(join(VECTORS, name), 'utf8')) as { cases: T[] }).cases;

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

// --- lo que está en el spec ----------------------------------------------------

describe('mensaje por omisión', () => {
  type Case = { note: string; notification: Record<string, unknown>; expect: { subject: string; text: string } };
  for (const test of load<Case>('mensaje.json')) {
    it(test.note, () => {
      const wire = test.notification;
      const rendered = renderEmail(
        notification({
          title: wire['title'] as string,
          body: wire['body'] as string | null,
          actionUrl: wire['action_url'] as string | null,
        }),
      );
      expect({ subject: rendered.subject, text: rendered.text }).toEqual(test.expect);
      expect(rendered.html ?? null).toBeNull();
    });
  }
});

describe('direcciones', () => {
  for (const test of load<{ note: string; address: unknown; valid: boolean }>('direcciones.json')) {
    it(test.note, () => {
      expect(isValidAddress(test.address)).toBe(test.valid);
    });
  }
});

// --- el canal ------------------------------------------------------------------

/** Un transporte que anota lo que le piden mandar. */
class Recorder implements EmailTransport {
  sent: EmailMessage[] = [];
  constructor(private readonly result: DeliveryResult | Error = { outcome: 'sent', providerMessageId: 'm-1' }) {}

  async send(message: EmailMessage): Promise<DeliveryResult> {
    this.sent.push(message);
    if (this.result instanceof Error) throw this.result;
    return this.result;
  }
}

const book: Record<string, string | null> = { ana: 'ana@example.com' };
const channel = (
  transport: EmailTransport,
  addresses: Record<string, string | null> = book,
  extra: Partial<ConstructorParameters<typeof EmailChannel>[0]> = {},
): EmailChannel =>
  new EmailChannel({
    transport,
    sender: 'avisos@example.com',
    resolveAddress: (id) => (Object.hasOwn(addresses, id) ? addresses[id] : null),
    ...extra,
  });

describe('el canal', () => {
  it('busca la dirección, arma el mensaje y lo manda', async () => {
    const transport = new Recorder();
    const result = await channel(transport, book, { senderName: 'Avisos' }).send(notification());
    expect(result).toEqual({ outcome: 'sent', providerMessageId: 'm-1' });
    expect(transport.sent).toEqual([
      {
        to: 'ana@example.com',
        sender: 'avisos@example.com',
        senderName: 'Avisos',
        subject: 'Ana comentó',
        text: '¿Va con IVA?\n\nhttps://example.com/doc/7',
        html: null,
        idempotencyKey: 'n1',
      },
    ]);
  });

  it('se llama email salvo que se le ponga otro nombre', () => {
    expect(channel(new Recorder()).id).toBe('email');
    expect(channel(new Recorder(), book, { id: 'email-facturacion' }).id).toBe('email-facturacion');
  });

  it('quien no tiene dirección se omite', async () => {
    const transport = new Recorder();
    expect((await channel(transport, { ana: null }).send(notification())).outcome).toBe('skipped');
    expect((await channel(transport, {}).send(notification())).outcome).toBe('skipped');
    expect(transport.sent).toEqual([]);
  });

  it('una dirección inválida se omite y no llega al transporte', async () => {
    for (const address of ['ana@example.com\nBcc: x@example.com', 'Ana <ana@example.com>', 'sin-arroba', '']) {
      const transport = new Recorder();
      const result = await channel(transport, { ana: address }).send(notification());
      expect([result.outcome, transport.sent]).toEqual(['skipped', []]);
    }
  });

  it('la dirección se puede buscar con una función asíncrona', async () => {
    const transport = new Recorder();
    const made = new EmailChannel({
      transport,
      sender: 'avisos@example.com',
      resolveAddress: async (id) => `${id}@example.com`,
    });
    expect((await made.send(notification())).outcome).toBe('sent');
    expect(transport.sent[0]?.to).toBe('ana@example.com');
  });

  it('si buscar la dirección falla es una entrega fallida', async () => {
    const made = new EmailChannel({
      transport: new Recorder(),
      sender: 'avisos@example.com',
      resolveAddress: () => {
        throw new Error('clave=secreta');
      },
    });
    const result = await made.send(notification());
    expect(result.outcome).toBe('failed');
    expect(result.error).not.toContain('secreta');
  });

  it('un transporte que rechaza es una entrega fallida', async () => {
    const result = await channel(new Recorder(new Error('sin conexión'))).send(notification());
    expect(result).toEqual({ outcome: 'failed', error: 'Error: sin conexión' });
  });

  it('un render propio se respeta y su asunto no puede partir la cabecera', async () => {
    const transport = new Recorder();
    await channel(transport, book, {
      render: (item) => ({ subject: `[${item.category}]\r\nBcc: x@example.com`, text: 'texto', html: '<b>hola</b>' }),
    }).send(notification());
    expect(transport.sent[0]?.subject).toBe('[activity] Bcc: x@example.com');
    expect([transport.sent[0]?.text, transport.sent[0]?.html]).toEqual(['texto', '<b>hola</b>']);
  });

  it('el remitente se valida al armar el canal', () => {
    for (const sender of ['', 'sin-arroba', 'Avisos <avisos@example.com>', 'a@b\nBcc: c@d']) {
      expect(() => new EmailChannel({ transport: new Recorder(), sender, resolveAddress: () => null })).toThrow(
        /sender/,
      );
    }
    for (const senderName of ['', '  ', 'Avisos\nBcc: x', 'Avisos <x>', 'Avi"sos']) {
      expect(() => channel(new Recorder(), book, { senderName })).toThrow(/senderName/);
    }
  });
});

// --- HTTP: un fetch inyectado ----------------------------------------------------

interface Call {
  url: string;
  init: RequestInit;
}

const fakeFetch = (
  respond: (call: Call) => Response | Promise<Response>,
): { calls: Call[]; fetch: (url: string, init: RequestInit) => Promise<Response> } => {
  const calls: Call[] = [];
  return {
    calls,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return respond({ url, init });
    },
  };
};
const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const MESSAGE: EmailMessage = {
  to: 'ana@example.com',
  sender: 'avisos@example.com',
  senderName: 'Avisos Ñandú',
  subject: 'Ana comentó — ¿revisás? 🔔',
  text: 'Línea uno\n\nhttps://example.com/doc/7',
  html: null,
  idempotencyKey: 'n1',
};

describe('transporte HTTP', () => {
  it('manda el mensaje como JSON', async () => {
    const fake = fakeFetch(() => json({ id: 'x' }));
    const transport = new HttpEmailTransport('https://mail.example.com/v1/send', {
      headers: { 'X-Api-Key': 'k-1' },
      fetch: fake.fetch,
    });
    expect(await transport.send(MESSAGE)).toEqual({ outcome: 'sent' });
    const [call] = fake.calls;
    expect(call?.url).toBe('https://mail.example.com/v1/send');
    expect(call?.init.method).toBe('POST');
    expect(call?.init.redirect).toBe('manual');
    expect(call?.init.signal).toBeInstanceOf(AbortSignal);
    expect(call?.init.headers).toEqual({ 'X-Api-Key': 'k-1', 'Content-Type': 'application/json' });
    expect(JSON.parse(call?.init.body as string)).toEqual({
      from: 'Avisos Ñandú <avisos@example.com>',
      to: 'ana@example.com',
      subject: MESSAGE.subject,
      text: MESSAGE.text,
    });
  });

  it('el cuerpo, las cabeceras y el identificador se pueden adaptar', async () => {
    const fake = fakeFetch(() => json({ data: { message_id: 'abc' } }));
    const transport = new HttpEmailTransport('https://mail.example.com', {
      buildBody: (m) => ({ destinatario: m.to, asunto: m.subject, contenido: { texto: m.text } }),
      buildHeaders: (m) => ({ 'X-Mensaje': m.idempotencyKey ?? '' }),
      readId: (response) => (response as { data: { message_id: string } }).data.message_id,
      fetch: fake.fetch,
    });
    expect(await transport.send(MESSAGE)).toEqual({ outcome: 'sent', providerMessageId: 'abc' });
    expect(JSON.parse(fake.calls[0]?.init.body as string)).toEqual({
      destinatario: 'ana@example.com',
      asunto: MESSAGE.subject,
      contenido: { texto: MESSAGE.text },
    });
    expect((fake.calls[0]?.init.headers as Record<string, string>)['X-Mensaje']).toBe('n1');
  });

  it('con html', async () => {
    const fake = fakeFetch(() => json({}));
    await new HttpEmailTransport('https://mail.example.com', { fetch: fake.fetch }).send({
      ...MESSAGE,
      html: '<b>h</b>',
    });
    expect(JSON.parse(fake.calls[0]?.init.body as string).html).toBe('<b>h</b>');
  });

  it('una respuesta que no es 2xx es una entrega fallida, sin repetir el cuerpo del error', async () => {
    for (const status of [307, 400, 401, 422, 429, 500, 503]) {
      const fake = fakeFetch(() => json({ error: 'clave k-secreta inválida' }, status));
      const transport = new HttpEmailTransport('https://mail.example.com', {
        headers: { 'X-Api-Key': 'k-secreta' },
        fetch: fake.fetch,
      });
      expect(await transport.send(MESSAGE)).toEqual({ outcome: 'failed', error: `HTTP ${status}` });
    }
  });

  it('una respuesta que no es JSON igual es enviado', async () => {
    const fake = fakeFetch(() => new Response('ok', { status: 200 }));
    const transport = new HttpEmailTransport('https://mail.example.com', {
      readId: (response) => (response as { id: string }).id,
      fetch: fake.fetch,
    });
    expect(await transport.send(MESSAGE)).toEqual({ outcome: 'sent' });
  });

  it('un error de red es una entrega fallida, sin el mensaje del error', async () => {
    const fake = fakeFetch(() => {
      throw new TypeError('fetch failed: https://usuario:clave@mail.example.com');
    });
    const result = await new HttpEmailTransport('https://mail.example.com', { fetch: fake.fetch }).send(MESSAGE);
    expect(result).toEqual({ outcome: 'failed', error: 'HTTP: TypeError' });
  });

  it('corta por tiempo agotado', async () => {
    const fake = fakeFetch(
      ({ init }) =>
        new Promise<Response>((_, reject) => {
          init.signal?.addEventListener('abort', () => reject(init.signal?.reason as Error));
        }),
    );
    const transport = new HttpEmailTransport('https://mail.example.com', { timeoutMs: 20, fetch: fake.fetch });
    expect(await transport.send(MESSAGE)).toEqual({ outcome: 'failed', error: 'HTTP: TimeoutError' });
  });

  it('la URL se valida', () => {
    for (const url of ['file:///etc/passwd', 'ftp://example.com', 'example.com/send', '']) {
      expect(() => new HttpEmailTransport(url)).toThrow(/url/);
    }
  });
});

describe('Resend', () => {
  it('arma el pedido de su API', async () => {
    const fake = fakeFetch(() => json({ id: 'msg_123' }));
    const transport = new ResendTransport('re_clave', { fetch: fake.fetch });
    expect(await transport.send({ ...MESSAGE, senderName: 'Avisos', subject: 's', text: 't' })).toEqual({
      outcome: 'sent',
      providerMessageId: 'msg_123',
    });
    const [call] = fake.calls;
    expect(call?.url).toBe('https://api.resend.com/emails');
    expect(call?.init.headers).toEqual({
      Authorization: 'Bearer re_clave',
      'Idempotency-Key': 'n1',
      'Content-Type': 'application/json',
    });
    expect(JSON.parse(call?.init.body as string)).toEqual({
      from: 'Avisos <avisos@example.com>',
      to: ['ana@example.com'],
      subject: 's',
      text: 't',
    });
  });

  it('otra base y la clave fuera del error', async () => {
    const fake = fakeFetch(() => json({ message: 're_clave no sirve' }, 403));
    const transport = new ResendTransport('re_clave', { baseUrl: 'https://resend.test/', fetch: fake.fetch });
    expect(await transport.send(MESSAGE)).toEqual({ outcome: 'failed', error: 'HTTP 403' });
    expect(fake.calls[0]?.url).toBe('https://resend.test/emails');
    expect(() => new ResendTransport('')).toThrow(/apiKey/);
  });
});

// --- de punta a punta -------------------------------------------------------------

it('de un evento a un mensaje entregado al transporte', async () => {
  const policy = parsePolicy({
    security: { inapp: { default: true, locked: true }, email: { default: true, locked: true } },
    activity: { inapp: { default: true, locked: true }, email: { default: false } },
  });
  const catalog = parseCatalog(
    {
      'login.new': { category: 'security', title: 'Nuevo inicio de sesión desde {device}' },
      'comment.new': { category: 'activity', title: '{author} comentó' },
    },
    policy,
  );
  const fake = fakeFetch(() => json({ id: 'msg_1' }));
  const store = new MemoryStore();
  const notifier = new Notifier({
    policy,
    catalog,
    store,
    preferences: new MemoryPreferenceStore(),
    channels: [
      new EmailChannel({
        transport: new ResendTransport('re_clave', { fetch: fake.fetch }),
        sender: 'avisos@example.com',
        resolveAddress: (id) => (id === 'ana' ? 'ana@example.com' : null),
      }),
    ],
  });
  const login = { type: 'login.new', payload: { device: 'Firefox' }, action_url: 'https://example.com/sesiones' };

  const sent = await notifier.notify({ ...login, recipient_id: 'ana' });
  expect(sent.deliveries.get('email')).toEqual({ outcome: 'sent', providerMessageId: 'msg_1' });
  expect(JSON.parse(fake.calls[0]?.init.body as string)).toEqual({
    from: 'avisos@example.com',
    to: ['ana@example.com'],
    subject: 'Nuevo inicio de sesión desde Firefox',
    text: 'https://example.com/sesiones',
  });
  expect((fake.calls[0]?.init.headers as Record<string, string>)['Idempotency-Key']).toBe(sent.notification.id);

  const quiet = await notifier.notify({ type: 'comment.new', recipient_id: 'ana', payload: { author: 'Beto' } });
  expect(quiet.deliveries.size).toBe(0);

  const nobody = await notifier.notify({ ...login, recipient_id: 'beto' });
  expect([nobody.created, nobody.deliveries.get('email')?.outcome]).toEqual([true, 'skipped']);
  expect(fake.calls).toHaveLength(1);
  expect(await store.countUnread('beto')).toBe(1);
});
