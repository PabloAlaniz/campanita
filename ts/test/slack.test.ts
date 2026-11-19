/** El canal de Slack y su conexión. Ningún test sale a la red. */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  escapeSlackText,
  exchangeSlackCode,
  isSlackWebhookUrl,
  MemoryPreferenceStore,
  MemoryStore,
  Notifier,
  parseCatalog,
  parsePolicy,
  parseSlackOAuthResponse,
  renderSlack,
  SlackChannel,
  slackAuthorizeUrl,
  SlackOAuthError,
  type Notification,
  type SlackChannelOptions,
} from '../src/index.js';

type MessageCase = { note: string; notification: { title: string; body: string | null; action_url: string | null }; text: string };
type WebhookCase = { note: string; url: unknown; valid: boolean };
type OAuthCase = { note: string; response: unknown; connection?: Record<string, string | null>; error?: string };
const CASES = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../fixtures/slack.json', import.meta.url)), 'utf8'),
) as { mensaje: MessageCase[]; webhooks: WebhookCase[]; oauth: OAuthCase[] };

const WEBHOOK = 'https://hooks.slack.com/services/T0001/B0001/secreto123';

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

const fakeFetch = (answer: () => Response | Promise<Response>) => {
  const calls: { url: string; init: RequestInit }[] = [];
  return {
    calls,
    fetch: async (url: string, init: RequestInit): Promise<Response> => {
      calls.push({ url, init });
      return answer();
    },
  };
};
const ok = (): Response => new Response('ok', { status: 200 });
const channel = (fake: { fetch: NonNullable<SlackChannelOptions['fetch']> }, options: Partial<SlackChannelOptions> = {}) =>
  new SlackChannel({ resolveWebhook: () => WEBHOOK, fetch: fake.fetch, ...options });

// --- los casos compartidos --------------------------------------------------------

describe('mensaje por omisión', () => {
  for (const test of CASES.mensaje) {
    it(test.note, () => {
      const { title, body, action_url: actionUrl } = test.notification;
      expect(renderSlack(notification({ title, body, actionUrl }))).toEqual({ text: test.text });
    });
  }
});

describe('webhooks', () => {
  for (const test of CASES.webhooks) {
    it(test.note, () => expect(isSlackWebhookUrl(test.url)).toBe(test.valid));
  }
});

describe('respuesta de la conexión', () => {
  for (const test of CASES.oauth) {
    it(test.note, () => {
      if (test.error !== undefined) {
        let caught: unknown;
        try {
          parseSlackOAuthResponse(test.response);
        } catch (error) {
          caught = error;
        }
        expect(caught).toBeInstanceOf(SlackOAuthError);
        expect((caught as SlackOAuthError).reason).toBe(test.error);
        return;
      }
      const c = test.connection!;
      expect(parseSlackOAuthResponse(test.response)).toEqual({
        webhookUrl: c['webhook_url'],
        channel: c['channel'],
        channelId: c['channel_id'],
        teamId: c['team_id'],
        teamName: c['team_name'],
        configurationUrl: c['configuration_url'],
      });
    });
  }
});

it('escape', () => {
  expect(escapeSlackText('a & b < c > d &amp;')).toBe('a &amp; b &lt; c &gt; d &amp;amp;');
});

// --- el canal ---------------------------------------------------------------------

describe('SlackChannel', () => {
  it('envía el mensaje al webhook de la persona', async () => {
    const fake = fakeFetch(ok);
    const asked: string[] = [];
    const made = channel(fake, {
      resolveWebhook: (id) => {
        asked.push(id);
        return WEBHOOK;
      },
    });
    expect(made.id).toBe('slack');
    expect(await made.send(notification())).toEqual({ outcome: 'sent' });
    expect(asked).toEqual(['ana']);
    expect(fake.calls).toHaveLength(1);
    const { url, init } = fake.calls[0]!;
    expect(url).toBe(WEBHOOK);
    expect(init.method).toBe('POST');
    expect(init.redirect).toBe('manual');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(JSON.parse(init.body as string)).toEqual({
      text: '*Ana comentó*\n¿Va con IVA?\n<https://example.com/doc/7>',
    });
  });

  it('con resolveWebhookFor el destino sale del aviso entero, por ejemplo de su ámbito', async () => {
    const fake = fakeFetch(ok);
    const byScope: Record<string, string> = { 'p-1': 'https://hooks.slack.com/services/T1/B1/uno' };
    const slack = new SlackChannel({ resolveWebhookFor: (n) => (n.scope ? byScope[n.scope] : null), fetch: fake.fetch });
    expect((await slack.send(notification({ scope: 'p-1' }))).outcome).toBe('sent');
    expect(fake.calls[0]!.url).toBe('https://hooks.slack.com/services/T1/B1/uno');
    expect((await slack.send(notification({ scope: 'p-2' }))).outcome).toBe('skipped');
    expect((await slack.send(notification())).outcome).toBe('skipped');
    expect(fake.calls).toHaveLength(1);
  });

  it('pide una sola forma de buscar el webhook', () => {
    expect(() => new SlackChannel({})).toThrow(TypeError);
    expect(() => new SlackChannel({ resolveWebhook: () => WEBHOOK, resolveWebhookFor: () => WEBHOOK })).toThrow(TypeError);
  });

  it('la búsqueda puede ser asincrónica', async () => {
    const fake = fakeFetch(ok);
    expect((await channel(fake, { resolveWebhook: async () => WEBHOOK }).send(notification())).outcome).toBe('sent');
    expect(fake.calls).toHaveLength(1);
  });

  it('sin conexión no se llama a Slack', async () => {
    for (const none of [null, undefined]) {
      const fake = fakeFetch(ok);
      const result = await channel(fake, { resolveWebhook: () => none }).send(notification());
      expect(result.outcome).toBe('skipped');
      expect(fake.calls).toEqual([]);
    }
  });

  it('una URL que no es de Slack no se usa', async () => {
    for (const url of ['https://example.com/services/T/B/a', 'http://hooks.slack.com/services/T/B/a', '']) {
      const fake = fakeFetch(ok);
      const result = await channel(fake, { resolveWebhook: () => url }).send(notification());
      expect(result.outcome).toBe('skipped');
      expect(fake.calls).toEqual([]);
    }
  });

  it('si la búsqueda falla es failed', async () => {
    const result = await channel(fakeFetch(ok), {
      resolveWebhook: () => {
        throw new RangeError(WEBHOOK);
      },
    }).send(notification());
    expect(result).toEqual({ outcome: 'failed', error: 'no se pudo buscar la conexión de Slack: RangeError' });
  });

  const answers: [number, string, string, string][] = [
    [404, 'no_service', 'expired', 'Slack 404 no_service'],
    [410, 'channel_is_archived\n', 'expired', 'Slack 410 channel_is_archived'],
    [400, 'invalid_payload', 'failed', 'Slack 400 invalid_payload'],
    [403, 'action_prohibited', 'failed', 'Slack 403 action_prohibited'],
    [500, '<html>Algo se rompió</html>', 'failed', 'Slack 500'],
    [429, '', 'failed', 'Slack 429'],
    [302, '', 'failed', 'Slack 302'],
  ];
  for (const [status, body, outcome, error] of answers) {
    it(`Slack contesta ${status} ${JSON.stringify(body)}`, async () => {
      const fake = fakeFetch(() => new Response(body, { status }));
      expect(await channel(fake).send(notification())).toEqual({ outcome, error });
    });
  }

  it('un error de red no repite la URL', async () => {
    const fake = fakeFetch(() => Promise.reject(new TypeError(`no se pudo conectar a ${WEBHOOK}`)));
    expect(await channel(fake).send(notification())).toEqual({ outcome: 'failed', error: 'Slack: TypeError' });
  });

  it('un mensaje propio', async () => {
    const fake = fakeFetch(ok);
    const made = channel(fake, { id: 'slack-equipo', render: (item) => ({ text: item.title, unfurl_links: false }) });
    expect((await made.send(notification())).outcome).toBe('sent');
    expect(made.id).toBe('slack-equipo');
    expect(JSON.parse(fake.calls[0]!.init.body as string)).toEqual({ text: 'Ana comentó', unfurl_links: false });
  });

  it('si el mensaje propio falla es failed y no se envía', async () => {
    const fake = fakeFetch(ok);
    const result = await channel(fake, {
      render: () => {
        throw new RangeError('x');
      },
    }).send(notification());
    expect(result).toEqual({ outcome: 'failed', error: 'no se pudo armar el mensaje: RangeError' });
    expect(fake.calls).toEqual([]);
  });

  it('de punta a punta con el Notifier', async () => {
    const policy = parsePolicy({ activity: { inapp: { default: true, locked: true }, slack: { default: true } } });
    const catalog = parseCatalog({ nota: { category: 'activity', title: '{author} comentó', body: '{text}' } }, policy);
    const fake = fakeFetch(ok);
    const notifier = new Notifier({
      policy,
      catalog,
      store: new MemoryStore(),
      preferences: new MemoryPreferenceStore(),
      channels: [new SlackChannel({ resolveWebhook: (id) => (id === 'ana' ? WEBHOOK : null), fetch: fake.fetch })],
    });
    const first = await notifier.notify({ type: 'nota', recipient_id: 'ana', payload: { author: 'Beto', text: '<hola>' } });
    const second = await notifier.notify({ type: 'nota', recipient_id: 'beto', payload: { author: 'Ana', text: 'x' } });
    expect(first.deliveries.get('slack')?.outcome).toBe('sent');
    expect(second.deliveries.get('slack')?.outcome).toBe('skipped');
    expect(fake.calls.map((call) => JSON.parse(call.init.body as string))).toEqual([
      { text: '*Beto comentó*\n&lt;hola&gt;' },
    ]);
  });
});

// --- conectar ---------------------------------------------------------------------

describe('conectar', () => {
  it('URL de autorización', () => {
    const url = new URL(
      slackAuthorizeUrl({ clientId: '123.456', redirectUri: 'https://app.example.com/slack/vuelta?x=1', state: 'a b&c' }),
    );
    expect(url.origin + url.pathname).toBe('https://slack.com/oauth/v2/authorize');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: '123.456',
      scope: 'incoming-webhook',
      redirect_uri: 'https://app.example.com/slack/vuelta?x=1',
      state: 'a b&c',
    });
  });

  it('URL de autorización pide todo', () => {
    const options = { clientId: '1', redirectUri: 'https://app.example.com/v', state: 's' };
    for (const missing of ['clientId', 'redirectUri', 'state'] as const) {
      expect(() => slackAuthorizeUrl({ ...options, [missing]: '' })).toThrow(new RegExp(missing));
    }
  });

  const oauth = { clientId: '123.456', clientSecret: 's3creto', code: 'c0digo', redirectUri: 'https://app.example.com/v' };
  const granted = (): Response => Response.json(CASES.oauth[0]!.response);

  it('cambia el código por la conexión', async () => {
    const fake = fakeFetch(granted);
    const connection = await exchangeSlackCode({ ...oauth, fetch: fake.fetch });
    expect(connection.webhookUrl).toBe('https://hooks.slack.com/services/T0001/B0001/abc');
    expect([connection.channel, connection.teamName]).toEqual(['#avisos', 'Equipo']);
    const { url, init } = fake.calls[0]!;
    expect(url).toBe('https://slack.com/api/oauth.v2.access');
    expect(init.method).toBe('POST');
    expect(init.redirect).toBe('manual');
    expect(init.headers).toEqual({ 'Content-Type': 'application/x-www-form-urlencoded' });
    expect(Object.fromEntries(new URLSearchParams(init.body as string))).toEqual({
      client_id: '123.456',
      client_secret: 's3creto',
      code: 'c0digo',
      redirect_uri: 'https://app.example.com/v',
    });
  });

  const failures: [string, () => Response | Promise<Response>, string][] = [
    ['Slack rechaza', () => Response.json({ ok: false, error: 'invalid_code' }), 'invalid_code'],
    ['un 500', () => new Response(JSON.stringify(CASES.oauth[0]!.response), { status: 500 }), 'http'],
    ['una redirección', () => new Response(null, { status: 302 }), 'http'],
    ['algo que no es JSON', () => new Response('<html>'), 'invalid_response'],
    ['la red', () => Promise.reject(new TypeError('s3creto c0digo')), 'network'],
  ];
  for (const [note, answer, reason] of failures) {
    it(`la conexión falla: ${note}`, async () => {
      const failure = await exchangeSlackCode({ ...oauth, fetch: fakeFetch(answer).fetch }).catch((error: unknown) => error);
      expect(failure).toBeInstanceOf(SlackOAuthError);
      const error = failure as SlackOAuthError;
      expect(error.reason).toBe(reason);
      expect(error.message).toBe(`no se pudo conectar con Slack: ${reason}`);
      expect(error.cause).toBeUndefined();
    });
  }

  it('la conexión pide todo', async () => {
    for (const missing of ['clientId', 'clientSecret', 'code', 'redirectUri'] as const) {
      const fake = fakeFetch(granted);
      await expect(exchangeSlackCode({ ...oauth, [missing]: '', fetch: fake.fetch })).rejects.toThrow(
        new RegExp(missing),
      );
      expect(fake.calls).toEqual([]);
    }
  });
});
