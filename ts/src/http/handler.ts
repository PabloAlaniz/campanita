/**
 * La bandeja por HTTP: un adaptador sobre la Web Fetch API (`Request` →
 * `Response`). Sirve igual en Workers, Hono, Next, Deno, Bun y Node 18+, sin
 * dependencias.
 *
 *     const inbox = createInboxHandler({
 *       store: new D1Store(env.DB),
 *       basePath: '/api/avisos',
 *       resolveRecipient: (request) => sesion(request)?.userId ?? null,
 *     });
 *     const response = await inbox(request);   // null: la ruta no es de la bandeja
 *
 * El contrato está en la sección "La bandeja por HTTP" del spec.
 */
import { effectiveMatrix, validatePatch } from '../core/channels.js';
import { checkCap } from '../core/inbox.js';
import { InvalidQuietHoursError, localMinute, parseQuietHours, type QuietHours } from '../core/quiet.js';
import { type Change, PatchRejectedError, type Policy } from '../core/types.js';
import type { ChannelAvailability, InboxReader, PreferenceStore, QuietHoursStore } from '../ports.js';
import {
  decodeCursor,
  encodeCursor,
  fromIso,
  itemToWire,
  type InboxErrorCode,
} from './wire.js';

type MaybePromise<T> = T | Promise<T>;

export interface InboxHandlerOptions {
  /** De dónde se lee la bandeja. */
  readonly store: InboxReader;
  /**
   * Quién hace el pedido: su `recipientId`, o `null` si no se sabe (responde
   * 401). Es la única fuente del destinatario: el pedido nunca lo elige.
   */
  readonly resolveRecipient: (request: Request) => MaybePromise<string | null>;
  /** El prefijo bajo el que se monta, sin barra final. Por omisión, la raíz. */
  readonly basePath?: string;
  /** Con política y almacén de preferencias, se sirven `/preferences`. */
  readonly policy?: Policy;
  readonly preferences?: PreferenceStore;
  /**
   * Los canales disponibles para esa persona en el ámbito de la consulta. Es la
   * misma función que recibe `Notifier`. Por omisión, todos los de la política.
   */
  readonly available?: ChannelAvailability;
  /** Con almacén de horarios, se sirve `/quiet-hours`. */
  readonly quietHours?: QuietHoursStore;
  /** La hora del servidor. Para pruebas. */
  readonly now?: () => Date;
}

export type InboxHandler = (request: Request) => Promise<Response | null>;

const MENSAJES: Record<InboxErrorCode, string> = {
  not_found: 'esa ruta no existe',
  method_not_allowed: 'esa ruta no acepta ese método',
  unauthenticated: 'no se sabe quién hace el pedido',
  invalid_limit: 'el límite tiene que ser un entero de 1 a 100',
  invalid_cursor: 'el cursor no es uno de los que devuelve la bandeja',
  invalid_cap: 'el tope tiene que ser un entero de 1 a 1000',
  invalid_query: 'la consulta tiene un parámetro inválido o repetido',
  invalid_body: 'el cuerpo del pedido no tiene la forma esperada',
  unknown_category: 'la categoría no está en la política',
  unknown_channel: 'el canal no está en esa categoría',
  invalid_value: 'el valor tiene que ser un booleano',
  locked: 'la celda está bloqueada',
  unavailable: 'el canal no está disponible',
  invalid_quiet_hours: 'el horario no es válido',
  unknown_timezone: 'la zona horaria no existe',
};

const HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' };

const json = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: HEADERS });

function fail(
  status: number,
  code: InboxErrorCode,
  extra: { category?: string; channel?: string | null; message?: string } = {},
): Response {
  const { message, ...where } = extra;
  return json(status, { error: { code, message: message ?? MENSAJES[code], ...where } });
}

const DECIMAL = /^[1-9][0-9]*$/;
const NO_BODY = Symbol('sin cuerpo válido');

function boundedInt(text: string | undefined, fallback: number, max: number): number | null {
  if (text === undefined) return fallback;
  return DECIMAL.test(text) && Number(text) <= max ? Number(text) : null;
}

async function readJson(request: Request): Promise<unknown> {
  try {
    return JSON.parse(await request.text()) as unknown;
  } catch {
    return NO_BODY;
  }
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Un pedido ya ubicado: quién pide, el pedido y su consulta. */
interface Call {
  readonly who: string;
  readonly request: Request;
  readonly params: ReadonlyMap<string, string>;
}

interface Route {
  readonly methods: readonly string[];
  readonly handle: (call: Call) => Promise<Response>;
}

export function createInboxHandler(options: InboxHandlerOptions): InboxHandler {
  const base = (options.basePath ?? '').replace(/\/+$/, '');
  const now = options.now ?? (() => new Date());
  const { store, policy, preferences, quietHours } = options;
  // Lo que necesita cada ruta opcional, ya comprobado: si falta, la ruta no existe.
  const prefsDeps = policy !== undefined && preferences !== undefined ? { policy, preferences } : null;

  /** La ruta de `path`: las fijas, y `/feed/{id}/read` con su `id` ya decodificado. */
  function match(path: string): Route | null {
    const fixed = routes.get(path);
    if (fixed !== undefined) return fixed;
    const encoded = /^\/feed\/([^/]+)\/read$/.exec(path)?.[1];
    if (encoded === undefined) return null;
    let id: string;
    try {
      id = decodeURIComponent(encoded);
    } catch {
      return null;
    }
    return {
      methods: ['POST'],
      handle: async ({ who }) => json(200, { changed: await store.markRead(id, who, now()) }),
    };
  }

  async function availableFor(recipientId: string, scope: string | null): Promise<string[]> {
    if (options.available) return [...(await options.available(recipientId, scope))];
    const channels = new Set<string>();
    for (const cells of Object.values(policy ?? {})) for (const channel of Object.keys(cells)) channels.add(channel);
    return [...channels];
  }

  async function feed(who: string, params: ReadonlyMap<string, string>): Promise<Response> {
    const limit = boundedInt(params.get('limit'), 20, 100);
    if (limit === null) return fail(400, 'invalid_limit');
    const unreadOnly = params.get('unread_only') ?? 'false';
    if (unreadOnly !== 'true' && unreadOnly !== 'false') return fail(400, 'invalid_query');
    let cursor = null;
    const cursorText = params.get('cursor');
    if (cursorText !== undefined) {
      cursor = decodeCursor(cursorText);
      if (cursor === null) return fail(400, 'invalid_cursor');
    }
    const page = await store.listFeed({
      recipientId: who,
      limit,
      cursor,
      unreadOnly: unreadOnly === 'true',
      category: params.get('category') ?? null,
      scope: params.get('scope') ?? null,
    });
    return json(200, {
      items: page.items.map(itemToWire),
      next_cursor: page.nextCursor === null ? null : encodeCursor(page.nextCursor),
    });
  }

  async function count(who: string, params: ReadonlyMap<string, string>): Promise<Response> {
    const cap = boundedInt(params.get('cap'), 99, 1000);
    if (cap === null) return fail(400, 'invalid_cap');
    const n = await store.countUnread(who, { scope: params.get('scope') ?? null, cap: checkCap(cap + 1) });
    return json(200, { count: Math.min(n, cap), capped: n > cap });
  }

  async function readAll(who: string, request: Request): Promise<Response> {
    const body = await readJson(request);
    if (!isObject(body) || Object.keys(body).some((k) => k !== 'before' && k !== 'scope' && k !== 'category')) {
      return fail(400, 'invalid_body');
    }
    const before = fromIso(body['before']);
    const textOrNull = (v: unknown): v is string | null | undefined =>
      v === undefined || v === null || typeof v === 'string';
    const { scope, category } = body;
    if (before === null || !textOrNull(scope) || !textOrNull(category)) return fail(400, 'invalid_body');
    const changed = await store.markAllRead(who, {
      before,
      readAt: now(),
      scope: scope ?? null,
      category: category ?? null,
    });
    return json(200, { changed });
  }

  async function prefs(
    deps: { policy: Policy; preferences: PreferenceStore },
    who: string,
    method: string,
    request: Request,
    scope: string | null,
  ): Promise<Response> {
    const available = await availableFor(who, scope);
    if (method === 'PATCH') {
      const body = await readJson(request);
      if (!isObject(body) || !Object.values(body).every(isObject)) return fail(400, 'invalid_body');
      let changes: Change[];
      try {
        changes = validatePatch(deps.policy, body as Record<string, Record<string, boolean>>, available);
      } catch (error) {
        if (!(error instanceof PatchRejectedError)) throw error;
        return fail(422, error.reason, { category: error.category, channel: error.channel, message: error.message });
      }
      await deps.preferences.saveChanges(who, changes, { scope });
    }
    const overrides = await deps.preferences.getOverrides(who, { scope });
    return json(200, { categories: effectiveMatrix(deps.policy, overrides, available) });
  }

  async function quiet(hours: QuietHoursStore, who: string, method: string, request: Request): Promise<Response> {
    if (method === 'PUT') {
      const body = await readJson(request);
      if (body === NO_BODY) return fail(400, 'invalid_body');
      let parsed: QuietHours;
      try {
        parsed = parseQuietHours(body);
      } catch (error) {
        if (!(error instanceof InvalidQuietHoursError)) throw error;
        return fail(422, 'invalid_quiet_hours', { message: error.message });
      }
      if (localMinute(parsed.timezone, now()) === null) return fail(422, 'unknown_timezone');
      await hours.setQuietHours(who, parsed);
    }
    return json(200, { quiet_hours: await hours.getQuietHours(who) });
  }

  // Las rutas fijas que sirve esta instalación: las opcionales sólo si tienen con qué.
  const routes = new Map<string, Route>([
    ['/feed', { methods: ['GET'], handle: ({ who, params }) => feed(who, params) }],
    ['/unread-count', { methods: ['GET'], handle: ({ who, params }) => count(who, params) }],
    ['/feed/read-all', { methods: ['POST'], handle: ({ who, request }) => readAll(who, request) }],
  ]);
  if (prefsDeps) {
    routes.set('/preferences', {
      methods: ['GET', 'PATCH'],
      handle: ({ who, request, params }) =>
        prefs(prefsDeps, who, request.method, request, params.get('scope') ?? null),
    });
  }
  if (quietHours) {
    routes.set('/quiet-hours', {
      methods: ['GET', 'PUT'],
      handle: ({ who, request }) => quiet(quietHours, who, request.method, request),
    });
  }

  // El orden de las respuestas es el del spec: ruta, método, quién pide y consulta.
  return async (request) => {
    const url = new URL(request.url);
    if (!url.pathname.startsWith(`${base}/`)) return null;
    const route = match(url.pathname.slice(base.length));
    if (route === null) return null;
    if (!route.methods.includes(request.method)) return fail(405, 'method_not_allowed');
    const who = await options.resolveRecipient(request);
    if (who === null) return fail(401, 'unauthenticated');

    const params = new Map<string, string>();
    for (const [key, value] of url.searchParams) {
      if (params.has(key)) return fail(400, 'invalid_query');
      params.set(key, value);
    }
    return route.handle({ who, request, params });
  };
}
