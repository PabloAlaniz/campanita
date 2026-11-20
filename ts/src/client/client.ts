/**
 * El cliente de la bandeja por HTTP: un método por ruta del contrato. No guarda
 * estado; para eso está `createInboxStore`.
 */
import type { QuietHours } from '../core/quiet.js';
import type { Overrides } from '../core/types.js';
import type {
  FeedResponse,
  InboxErrorBody,
  InboxErrorCode,
  PreferencesResponse,
  QuietHoursResponse,
  UnreadCountResponse,
} from '../http/wire.js';

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;
type HeadersSource = Record<string, string> | (() => Record<string, string> | Promise<Record<string, string>>);

export interface InboxClientOptions {
  /** La URL donde está montado el adaptador, por ejemplo `https://api.example.com/api/avisos`. */
  readonly baseUrl: string;
  /** Para pasar cookies, reintentos o un mock. Por omisión, el `fetch` global. */
  readonly fetch?: Fetch;
  /** Cabeceras de cada pedido, por ejemplo `authorization`. */
  readonly headers?: HeadersSource;
}

export interface FeedOptions {
  readonly limit?: number;
  readonly cursor?: string | null;
  readonly unreadOnly?: boolean;
  readonly category?: string | null;
  readonly scope?: string | null;
  readonly signal?: AbortSignal;
}

export interface ScopeOptions {
  readonly scope?: string | null;
  readonly signal?: AbortSignal;
}

/** El servidor contestó con un error del contrato, o con algo que no es del contrato. */
export class InboxRequestError extends Error {
  override readonly name = 'InboxRequestError';

  constructor(
    readonly status: number,
    /** `null` si la respuesta no trae un error del contrato (un proxy, un 500). */
    readonly code: InboxErrorCode | null,
    message: string,
    readonly category?: string,
    readonly channel?: string | null,
  ) {
    super(message);
  }
}

export interface InboxClient {
  feed(options?: FeedOptions): Promise<FeedResponse>;
  unreadCount(options?: ScopeOptions & { readonly cap?: number }): Promise<UnreadCountResponse>;
  markRead(id: string, options?: { readonly signal?: AbortSignal }): Promise<boolean>;
  /** `before`: el `created_at` del aviso más nuevo que la persona vio, más un milisegundo. */
  markAllRead(options: {
    readonly before: string;
    readonly scope?: string | null;
    readonly category?: string | null;
    readonly signal?: AbortSignal;
  }): Promise<number>;
  getPreferences(options?: ScopeOptions): Promise<PreferencesResponse>;
  updatePreferences(patch: Overrides, options?: ScopeOptions): Promise<PreferencesResponse>;
  getQuietHours(options?: { readonly signal?: AbortSignal }): Promise<QuietHours | null>;
  setQuietHours(quietHours: QuietHours, options?: { readonly signal?: AbortSignal }): Promise<QuietHours>;
}

export function createInboxClient(options: InboxClientOptions): InboxClient {
  const base = options.baseUrl.replace(/\/+$/, '');
  const doFetch: Fetch = options.fetch ?? ((input, init) => fetch(input, init));

  async function call<T>(
    method: string,
    path: string,
    params: Record<string, string | number | boolean | null | undefined> = {},
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) query.set(key, String(value));
    }
    const qs = query.toString();
    const headers: Record<string, string> = {
      accept: 'application/json',
      ...(typeof options.headers === 'function' ? await options.headers() : options.headers),
    };
    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers['content-type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (signal) init.signal = signal;
    const response = await doFetch(`${base}${path}${qs ? `?${qs}` : ''}`, init);
    let data: unknown = null;
    try {
      data = await response.json();
    } catch {
      // Una respuesta que no es JSON: un proxy o una página de error.
    }
    if (!response.ok) {
      const error = (data as Partial<InboxErrorBody> | null)?.error;
      if (error && typeof error.code === 'string') {
        throw new InboxRequestError(response.status, error.code, error.message, error.category, error.channel);
      }
      throw new InboxRequestError(response.status, null, `la bandeja respondió ${response.status}`);
    }
    return data as T;
  }

  return {
    feed: (o = {}) =>
      call('GET', '/feed', {
        limit: o.limit,
        cursor: o.cursor,
        unread_only: o.unreadOnly,
        category: o.category,
        scope: o.scope,
      }, undefined, o.signal),
    unreadCount: (o = {}) => call('GET', '/unread-count', { scope: o.scope, cap: o.cap }, undefined, o.signal),
    markRead: async (id, o = {}) =>
      (
        await call<{ changed: boolean }>('POST', `/feed/${encodeURIComponent(id)}/read`, {}, undefined, o.signal)
      ).changed,
    markAllRead: async (o) => {
      const body: Record<string, string | null> = { before: o.before };
      if (o.scope !== undefined) body['scope'] = o.scope;
      if (o.category !== undefined) body['category'] = o.category;
      return (await call<{ changed: number }>('POST', '/feed/read-all', {}, body, o.signal)).changed;
    },
    getPreferences: (o = {}) => call('GET', '/preferences', { scope: o.scope }, undefined, o.signal),
    updatePreferences: (patch, o = {}) => call('PATCH', '/preferences', { scope: o.scope }, patch, o.signal),
    getQuietHours: async (o = {}) =>
      (await call<QuietHoursResponse>('GET', '/quiet-hours', {}, undefined, o.signal)).quiet_hours,
    setQuietHours: async (quietHours, o = {}) =>
      (await call<QuietHoursResponse>('PUT', '/quiet-hours', {}, quietHours, o.signal)).quiet_hours!,
  };
}
