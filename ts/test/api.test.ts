/** Lo que los vectores no cubren: la forma de la API en TypeScript. */
import { expect, it } from 'vitest';
import {
  buildNotification,
  effectiveMatrix,
  EventRejectedError,
  InvalidCatalogError,
  InvalidPolicyError,
  parseCatalog,
  parsePolicy,
  PatchRejectedError,
  resolveChannels,
  validatePatch,
} from '../src/index.js';

const raw = {
  security: { inapp: { default: true, locked: true }, email: { default: true, locked: true } },
  activity: { inapp: { default: true, locked: true }, email: { default: false } },
};

it('la política es inmutable', () => {
  const policy = parsePolicy(raw);
  expect(Object.isFrozen(policy)).toBe(true);
  expect(Object.isFrozen(policy['activity'])).toBe(true);
  expect(Object.isFrozen(policy['activity']?.['email'])).toBe(true);
  expect(policy['activity']?.['email']).toEqual({ default: false, locked: false, urgent: false });
});

it('cambiar el dato original no cambia la política', () => {
  const source = structuredClone(raw);
  const policy = parsePolicy(source);
  source.activity.email.default = true;
  expect(policy['activity']?.['email']?.default).toBe(false);
});

it('la política no hereda propiedades', () => {
  const policy = parsePolicy(raw);
  expect(policy['constructor']).toBeUndefined();
  expect(policy['activity']?.['toString']).toBeUndefined();
});

it('el error de política dice dónde', () => {
  expect(() => parsePolicy({ activity: { inapp: { default: true, locked: true }, email: {} } })).toThrow(
    new InvalidPolicyError('activity.email: falta `default`'),
  );
});

it('el rechazo lleva categoría, canal y motivo', () => {
  expect(() => validatePatch(parsePolicy(raw), { security: { email: false } }, ['email'])).toThrow(
    new PatchRejectedError('security', 'email', 'locked'),
  );
  expect(new PatchRejectedError('security', 'email', 'locked').message).toBe(
    'cambio de preferencias rechazado en security.email: la celda está bloqueada',
  );
});

it('los argumentos opcionales son sin overrides y sin canales', () => {
  const policy = parsePolicy(raw);
  expect(resolveChannels(policy, 'security')).toEqual([]);
  expect(effectiveMatrix(policy)['security']?.['email']).toEqual({ enabled: true, locked: true });
});

it('los canales disponibles pueden ser cualquier iterable', () => {
  expect(resolveChannels(parsePolicy(raw), 'security', {}, new Set(['email']))).toEqual(['email']);
});

const rawCatalog = {
  'comments.new': { category: 'activity', title: '{author} comentó', body: '{excerpt}' },
};

it('el catálogo es inmutable y no hereda propiedades', () => {
  const catalog = parseCatalog(rawCatalog, parsePolicy(raw));
  expect(Object.isFrozen(catalog)).toBe(true);
  expect(Object.isFrozen(catalog['comments.new'])).toBe(true);
  expect(catalog['comments.new']).toEqual({ category: 'activity', title: '{author} comentó', body: '{excerpt}' });
  expect(catalog['constructor']).toBeUndefined();
});

it('el error de catálogo dice dónde', () => {
  const policy = parsePolicy(raw);
  expect(() => parseCatalog({ 'comments.new': { category: 'marketing', title: 'x' } }, policy)).toThrow(
    new InvalidCatalogError('comments.new: `category` tiene que ser una categoría de la política'),
  );
  expect(() => parseCatalog({ 'comments.new': { category: 'activity', title: 'Hola}' } }, policy)).toThrow(
    new InvalidCatalogError('comments.new: llave de cierre suelta en la posición 4'),
  );
});

it('cambiar el evento después no cambia el aviso', () => {
  const catalog = parseCatalog(rawCatalog, parsePolicy(raw));
  const payload = { author: 'Ana', excerpt: 'Hola' };
  const draft = buildNotification(catalog, { type: 'comments.new', recipient_id: 'u1', payload });
  payload.author = 'Otra';
  expect(draft).toEqual({
    recipientId: 'u1',
    type: 'comments.new',
    category: 'activity',
    title: 'Ana comentó',
    body: 'Hola',
    actionUrl: null,
    payload: { author: 'Ana', excerpt: 'Hola' },
    scope: null,
    dedupeKey: null,
  });
});

it('el rechazo del evento lleva motivo y detalle', () => {
  const catalog = parseCatalog(rawCatalog, parsePolicy(raw));
  expect(() => buildNotification(catalog, { type: 'comments.new', recipient_id: 'u1' })).toThrow(
    new EventRejectedError('missing_variable', 'author'),
  );
  expect(new EventRejectedError('missing_variable', 'author').message).toBe(
    'evento rechazado: falta una variable (author)',
  );
});

it('un catálogo que no salió de parseCatalog no sirve para armar avisos', () => {
  const fake = { 'comments.new': { category: 'activity', title: 'x', body: null } } as never;
  expect(() => buildNotification(fake, { type: 'comments.new', recipient_id: 'u1' })).toThrow(TypeError);
});
