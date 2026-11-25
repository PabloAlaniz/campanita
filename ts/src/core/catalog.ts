/**
 * El catálogo de tipos y el armado del aviso.
 *
 * Quien emite manda `type` y `payload`, y no sabe nada de categorías ni de
 * textos: eso lo decide el catálogo, en un solo lugar.
 */
import { parseTemplate, renderParsed, type Parsed } from './templates.js';
import {
  EventRejectedError,
  InvalidCatalogError,
  InvalidTemplateError,
  TemplateError,
  type Catalog,
  type Draft,
  type Policy,
  type TypeSpec,
} from './types.js';

const TYPE_ID = /^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$/;
const MAX_TYPE_ID = 128;
const ENTRY_KEYS = new Set(['category', 'title', 'body', 'audience']);
const OPTIONAL_TEXT = ['action_url', 'scope', 'dedupe_key'] as const;
const EVENT_KEYS = new Set<string>(['type', 'recipient_id', 'payload', ...OPTIONAL_TEXT]);
/**
 * Lo que cuenta como espacio para decidir si un título está vacío. Es una lista
 * fija a propósito: "espacio" no significa lo mismo en cada lenguaje.
 */
const NOT_BLANK = /[^ \t\n\r]/;

/** Las plantillas ya separadas de cada catálogo, para no separarlas en cada aviso. */
const templates = new WeakMap<Catalog, Map<string, { title: Parsed; body: Parsed | null }>>();

/**
 * Valida un catálogo con forma de JSON contra `policy`.
 *
 * Lanza `InvalidCatalogError` con la ubicación del primer problema.
 */
export function parseCatalog(raw: unknown, policy: Policy): Catalog {
  if (!isObject(raw)) {
    throw new InvalidCatalogError('el catálogo tiene que ser un objeto de tipos');
  }

  const types: Record<string, TypeSpec> = Object.create(null);
  const parsed = new Map<string, { title: Parsed; body: Parsed | null }>();
  for (const typeId of Object.keys(raw)) {
    if (typeId.length > MAX_TYPE_ID || !TYPE_ID.test(typeId)) {
      throw new InvalidCatalogError(`tipo ${JSON.stringify(typeId)}: no es un identificador de tipo`);
    }
    const { spec, title, body } = parseEntry(typeId, raw[typeId], policy);
    types[typeId] = spec;
    parsed.set(typeId, { title, body });
  }

  const catalog = Object.freeze(types) as Catalog;
  templates.set(catalog, parsed);
  return catalog;
}

/** Valida un tipo del catálogo. Los errores salen en el orden que fija el spec. */
function parseEntry(
  typeId: string,
  entry: unknown,
  policy: Policy,
): { spec: TypeSpec; title: Parsed; body: Parsed | null } {
  if (!isObject(entry)) {
    throw new InvalidCatalogError(`${typeId}: tiene que ser un objeto`);
  }
  const extra = Object.keys(entry)
    .filter((key) => !ENTRY_KEYS.has(key))
    .sort();
  if (extra[0] !== undefined) {
    throw new InvalidCatalogError(`${typeId}: clave desconocida \`${extra[0]}\``);
  }

  const category = own(entry, 'category');
  if (typeof category !== 'string' || !Object.hasOwn(policy, category)) {
    throw new InvalidCatalogError(`${typeId}: \`category\` tiene que ser una categoría de la política`);
  }
  if (!Object.hasOwn(entry, 'title')) {
    throw new InvalidCatalogError(`${typeId}: falta \`title\``);
  }
  const title = entry['title'];
  const hasBody = Object.hasOwn(entry, 'body');
  const body = hasBody ? entry['body'] : null;
  const { titleParts, bodyParts } = parseTexts(typeId, title, hasBody, body);

  const audience = Object.hasOwn(entry, 'audience') ? entry['audience'] : 'person';
  if (audience !== 'person' && audience !== 'group') {
    throw new InvalidCatalogError(`${typeId}: \`audience\` tiene que ser \`person\` o \`group\``);
  }

  const spec = Object.freeze({ category, title: title as string, body: body as string | null, audience });
  return { spec, title: titleParts, body: bodyParts };
}

/** Separa título y cuerpo. El título no puede quedar vacío. */
function parseTexts(
  typeId: string,
  title: unknown,
  hasBody: boolean,
  body: unknown,
): { titleParts: Parsed; bodyParts: Parsed | null } {
  let titleParts: Parsed;
  let bodyParts: Parsed | null;
  try {
    titleParts = parseTemplate(title);
    bodyParts = hasBody ? parseTemplate(body) : null;
  } catch (error) {
    if (error instanceof InvalidTemplateError) {
      throw new InvalidCatalogError(`${typeId}: ${error.message}`);
    }
    throw error;
  }
  if (titleParts.length === 0) {
    throw new InvalidCatalogError(`${typeId}: \`title\` no puede ser vacío`);
  }
  return { titleParts, bodyParts };
}

/**
 * Arma el aviso de un evento, o lanza `EventRejectedError`.
 *
 * El orden de las revisiones es el del spec: el tipo, los campos conocidos, los
 * desconocidos, el título y por último el cuerpo.
 */
export function buildNotification(catalog: Catalog, event: Readonly<Record<string, unknown>>): Draft {
  const typeId = own(event, 'type');
  const spec = typeof typeId === 'string' ? own(catalog, typeId) : undefined;
  if (typeof typeId !== 'string' || spec === undefined) {
    throw new EventRejectedError('unknown_type', typeof typeId === 'string' ? typeId : null);
  }

  const recipientId = own(event, 'recipient_id');
  if (typeof recipientId !== 'string' || recipientId === '') {
    throw new EventRejectedError('invalid_field', 'recipient_id');
  }
  const payload = Object.hasOwn(event, 'payload') ? event['payload'] : {};
  if (!isObject(payload)) {
    throw new EventRejectedError('invalid_field', 'payload');
  }
  const optional: Record<string, string | null> = {};
  for (const name of OPTIONAL_TEXT) {
    const value = own(event, name) ?? null;
    if (value !== null && (typeof value !== 'string' || value === '')) {
      throw new EventRejectedError('invalid_field', name);
    }
    optional[name] = value;
  }
  // Un campo que no existe se rechaza: `dedup_key` mal escrito no puede
  // perderse en silencio y dejar pasar duplicados.
  const unknown = Object.keys(event)
    .filter((key) => !EVENT_KEYS.has(key))
    .sort(byCodePoint);
  if (unknown[0] !== undefined) {
    throw new EventRejectedError('invalid_field', unknown[0]);
  }

  const parts = templates.get(catalog)?.get(typeId);
  if (parts === undefined) {
    throw new TypeError('el catálogo no salió de parseCatalog()');
  }
  let title: string;
  let body: string | null;
  try {
    title = renderParsed(parts.title, payload);
    if (!NOT_BLANK.test(title)) throw new EventRejectedError('empty_title', null);
    body = parts.body === null ? null : renderParsed(parts.body, payload);
  } catch (error) {
    if (error instanceof TemplateError) throw new EventRejectedError(error.reason, error.variable);
    throw error;
  }

  return {
    recipientId,
    type: typeId,
    category: spec.category,
    title,
    body,
    actionUrl: optional['action_url'] ?? null,
    // Una copia: cambiar el evento después no cambia el aviso.
    payload: { ...payload },
    scope: optional['scope'] ?? null,
    dedupeKey: optional['dedupe_key'] ?? null,
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function own<T>(object: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.hasOwn(object, key) ? object[key] : undefined;
}

/** Orden por punto de código Unicode, como pide el spec. */
function byCodePoint(a: string, b: string): number {
  const left = a[Symbol.iterator]();
  const right = b[Symbol.iterator]();
  for (;;) {
    const x = left.next();
    const y = right.next();
    if (x.done || y.done) return (x.done ? 0 : 1) - (y.done ? 0 : 1);
    const diff = x.value.codePointAt(0)! - y.value.codePointAt(0)!;
    if (diff !== 0) return diff;
  }
}
