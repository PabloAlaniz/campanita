/** Lo que los vectores no cubren: la forma de la API en TypeScript. */
import { expect, it } from 'vitest';
import {
  effectiveMatrix,
  InvalidPolicyError,
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
  expect(policy['activity']?.['email']).toEqual({ default: false, locked: false });
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
