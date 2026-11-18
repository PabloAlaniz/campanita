/**
 * Por qué canales sale un aviso y qué puede cambiar el usuario.
 *
 * Son funciones puras a propósito: no tocan base de datos ni red, y por eso se
 * prueban como una tabla de casos (los vectores de `spec/vectors/`).
 */
import {
  INBOX,
  PatchRejectedError,
  type Cell,
  type Change,
  type Matrix,
  type MatrixCell,
  type Overrides,
  type Policy,
} from './types.js';

const NO_OVERRIDES: Overrides = Object.freeze({});

/**
 * Los canales de entrega por los que sale un aviso de `category`, ordenados.
 *
 * Nunca incluye `inapp`: la notificación guardada ya es la entrega in-app. Una
 * categoría que no está en la política no sale por ningún canal.
 */
export function resolveChannels(
  policy: Policy,
  category: string,
  overrides: Overrides = NO_OVERRIDES,
  available: Iterable<string> = [],
): string[] {
  const cells = own(policy, category);
  if (cells === undefined) return [];
  const can = new Set(available);
  const mine = own(overrides, category);
  return Object.keys(cells)
    .filter((channel) => channel !== INBOX && can.has(channel) && enabled(cells[channel]!, mine, channel))
    .sort(byCodePoint);
}

/**
 * La matriz completa que muestra una pantalla de preferencias.
 *
 * La interfaz no recalcula la política: la muestra. Un canal que no está
 * disponible se informa bloqueado y conserva su `enabled`.
 */
export function effectiveMatrix(
  policy: Policy,
  overrides: Overrides = NO_OVERRIDES,
  available: Iterable<string> = [],
): Matrix {
  const can = new Set(available);
  const matrix: Matrix = Object.create(null);
  for (const category of Object.keys(policy)) {
    const cells = policy[category]!;
    const mine = own(overrides, category);
    const row: Record<string, MatrixCell> = Object.create(null);
    for (const channel of Object.keys(cells)) {
      const cell = cells[channel]!;
      row[channel] = {
        enabled: enabled(cell, mine, channel),
        locked: cell.locked || (channel !== INBOX && !can.has(channel)),
      };
    }
    matrix[category] = row;
  }
  return matrix;
}

/**
 * Revisa un cambio de preferencias y devuelve lo que hay que guardar.
 *
 * Lanza `PatchRejectedError` ante la primera celda que no se puede cambiar. Una
 * celda bloqueada no se ignora en silencio: si una interfaz la mandó, tiene un
 * error y conviene que se entere.
 */
export function validatePatch(
  policy: Policy,
  patch: Readonly<Record<string, Readonly<Record<string, unknown>>>>,
  available: Iterable<string> = [],
): Change[] {
  const can = new Set(available);
  const changes: Change[] = [];
  for (const category of Object.keys(patch).sort(byCodePoint)) {
    const cells = own(policy, category);
    if (cells === undefined) throw new PatchRejectedError(category, null, 'unknown_category');
    const wanted = patch[category]!;
    for (const channel of Object.keys(wanted).sort(byCodePoint)) {
      const value = wanted[channel];
      const cell = own(cells, channel);
      if (cell === undefined) throw new PatchRejectedError(category, channel, 'unknown_channel');
      if (typeof value !== 'boolean') throw new PatchRejectedError(category, channel, 'invalid_value');
      if (cell.locked) throw new PatchRejectedError(category, channel, 'locked');
      if (!can.has(channel)) throw new PatchRejectedError(category, channel, 'unavailable');
      changes.push({ category, channel, enabled: value });
    }
  }
  return changes;
}

function enabled(
  cell: Cell,
  overrides: Readonly<Record<string, boolean>> | undefined,
  channel: string,
): boolean {
  if (cell.locked) return cell.default;
  return (overrides === undefined ? undefined : own(overrides, channel)) ?? cell.default;
}

/**
 * La propiedad PROPIA de un objeto. Los overrides y el patch llegan de afuera
 * como objetos comunes, donde `obj['constructor']` devuelve una función heredada.
 */
function own<T>(object: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.hasOwn(object, key) ? object[key] : undefined;
}

/**
 * Orden por punto de código Unicode, como pide el spec. El `sort()` por defecto
 * compara unidades UTF-16 y da otro resultado fuera del plano básico.
 */
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
