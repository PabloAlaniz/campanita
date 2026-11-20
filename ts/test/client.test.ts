/** El cliente y el store contra el adaptador de verdad, sobre un almacén en memoria. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryStore, type Draft } from '../src/index.js';
import { createInboxHandler } from '../src/http/index.js';
import { createInboxClient, createInboxStore, InboxRequestError } from '../src/client/index.js';

const draft = (changes: Partial<Draft> = {}): Draft => ({
  recipientId: 'ana',
  type: 'nota',
  category: 'activity',
  title: 'Aviso',
  body: null,
  actionUrl: null,
  payload: {},
  scope: null,
  dedupeKey: null,
  ...changes,
});

function setup(options: { failMarks?: boolean } = {}) {
  const store = new MemoryStore();
  const handle = createInboxHandler({ store, basePath: '/avisos', resolveRecipient: () => 'ana' });
  const calls: string[] = [];
  const fetch = async (input: string, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    calls.push(`${request.method} ${new URL(request.url).pathname}${new URL(request.url).search}`);
    if (options.failMarks && request.method === 'POST') return new Response('caído', { status: 502 });
    return (await handle(request)) ?? new Response(null, { status: 404 });
  };
  const client = createInboxClient({ baseUrl: 'https://api.test/avisos/', fetch });
  const add = (id: string, at: number, changes: Partial<Draft> = {}) =>
    store.add(draft(changes), { id, createdAt: new Date(at) });
  return { store, client, calls, add };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('el cliente', () => {
  it('lee, cuenta y marca', async () => {
    const { client, add } = setup();
    await add('n1', 1000);
    await add('n2', 2000, { scope: 'acme' });
    await add('n3', 3000, { scope: 'otro' });
    expect((await client.feed({ scope: 'acme' })).items.map((i) => i.id)).toEqual(['n2', 'n1']);
    expect(await client.unreadCount({ cap: 2 })).toEqual({ count: 2, capped: true });
    expect(await client.markRead('n1')).toBe(true);
    expect(await client.markAllRead({ before: new Date(2001).toISOString(), scope: 'acme' })).toBe(1);
    expect(await client.unreadCount()).toEqual({ count: 1, capped: false });
  });

  it('un error del contrato llega con su código', async () => {
    const { client } = setup();
    const error = await client.feed({ limit: 500 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(InboxRequestError);
    expect(error).toMatchObject({ status: 400, code: 'invalid_limit' });
  });

  it('una respuesta que no es del contrato llega sin código', async () => {
    const { client } = setup({ failMarks: true });
    await expect(client.markRead('x')).rejects.toMatchObject({ status: 502, code: null });
  });
});

describe('el store', () => {
  it('carga al suscribirse y mira el contador mientras alguien escucha', async () => {
    vi.useFakeTimers();
    const { client, calls, add } = setup();
    await add('n1', 1000);
    const inbox = createInboxStore(client, { pollMs: 1000 });
    const seen: number[] = [];
    const off = inbox.subscribe(() => seen.push(inbox.getSnapshot().unread.count));
    await vi.waitFor(() => expect(inbox.getSnapshot().status).toBe('ready'));
    expect(inbox.getSnapshot().items.map((i) => i.id)).toEqual(['n1']);

    calls.length = 0;
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toEqual(['GET /avisos/unread-count?cap=99']);

    await add('n2', 2000);
    calls.length = 0;
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toContain('GET /avisos/feed?limit=20');
    expect(inbox.getSnapshot().items.map((i) => i.id)).toEqual(['n2', 'n1']);
    expect(seen.at(-1)).toBe(2);

    off();
    calls.length = 0;
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toEqual([]);
  });

  it('pagina sin repetir', async () => {
    const { client, add } = setup();
    for (let i = 1; i <= 5; i += 1) await add(`n${i}`, i * 1000);
    const inbox = createInboxStore(client, { pageSize: 2, pollMs: 0 });
    await inbox.refresh();
    expect(inbox.getSnapshot().hasMore).toBe(true);
    await inbox.loadMore();
    await inbox.loadMore();
    expect(inbox.getSnapshot().items.map((i) => i.id)).toEqual(['n5', 'n4', 'n3', 'n2', 'n1']);
    expect(inbox.getSnapshot().hasMore).toBe(false);
  });

  it('marcar todo corta en lo que se vio, no en lo que llegó después', async () => {
    const { client, add, store } = setup();
    await add('n1', 1000);
    await add('n2', 2000);
    const inbox = createInboxStore(client, { pollMs: 0 });
    await inbox.refresh();
    await add('n3', 3000);
    await inbox.markAllRead();
    const page = await store.listFeed({ recipientId: 'ana', limit: 10 });
    expect(page.items.map((n) => [n.id, n.readAt !== null])).toEqual([
      ['n3', false],
      ['n2', true],
      ['n1', true],
    ]);
    expect(inbox.getSnapshot().unread).toEqual({ count: 1, capped: false });
  });

  it('el mismo milisegundo que el último visto también se marca', async () => {
    const { client, add, store } = setup();
    await add('n1', 1000);
    const inbox = createInboxStore(client, { pollMs: 0 });
    await inbox.refresh();
    await inbox.markAllRead();
    expect(await store.countUnread('ana')).toBe(0);
  });

  it('si el servidor falla, vuelve atrás y avisa', async () => {
    const { client, add } = setup({ failMarks: true });
    await add('n1', 1000);
    const inbox = createInboxStore(client, { pollMs: 0 });
    await inbox.refresh();
    const states: Array<string | null> = [];
    inbox.subscribe(() => states.push(inbox.getSnapshot().items[0]!.read_at));
    await inbox.markRead('n1');
    expect(states[0]).not.toBeNull();
    expect(inbox.getSnapshot().items[0]!.read_at).toBeNull();
    expect(inbox.getSnapshot().unread.count).toBe(1);
    expect(inbox.getSnapshot().error).toBeInstanceOf(InboxRequestError);
    inbox.stop();
  });
});
