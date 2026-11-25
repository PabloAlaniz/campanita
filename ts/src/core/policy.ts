import { ID, INBOX, InvalidPolicyError, type Cell, type Policy } from './types.js';

/**
 * Valida una política con forma de JSON y la devuelve inmutable.
 *
 * Lanza `InvalidPolicyError` con la ubicación del primer problema.
 */
export function parsePolicy(raw: unknown): Policy {
  if (!isObject(raw)) {
    throw new InvalidPolicyError('la política tiene que ser un objeto de categorías');
  }
  const names = Object.keys(raw);
  if (names.length === 0) {
    throw new InvalidPolicyError('la política no tiene categorías');
  }

  // Sin prototipo: una categoría llamada `constructor` es un dato más, y una
  // que no existe no se confunde con una propiedad heredada.
  const categories: Record<string, Readonly<Record<string, Cell>>> = Object.create(null);
  for (const category of names) {
    checkId(category, 'categoría');
    const cells = raw[category];
    if (!isObject(cells)) {
      throw new InvalidPolicyError(`${category}: tiene que ser un objeto de canales`);
    }

    const parsed: Record<string, Cell> = Object.create(null);
    for (const channel of Object.keys(cells)) {
      checkId(channel, `${category}: canal`);
      parsed[channel] = parseCell(cells[channel], `${category}.${channel}`);
    }

    // La bandeja siempre recibe: no es un canal opcional, es donde la
    // notificación vive.
    const inbox = parsed[INBOX];
    if (inbox === undefined || !inbox.default || !inbox.locked) {
      throw new InvalidPolicyError(
        `${category}: falta \`${INBOX}\` con \`default: true\` y \`locked: true\``,
      );
    }
    categories[category] = Object.freeze(parsed);
  }

  return Object.freeze(categories) as Policy;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function checkId(value: string, what: string): void {
  if (!ID.test(value)) {
    throw new InvalidPolicyError(
      `${what} ${JSON.stringify(value)}: no es un identificador ([a-z][a-z0-9_-]*, hasta 64 caracteres)`,
    );
  }
}

function parseCell(cell: unknown, where: string): Cell {
  if (!isObject(cell)) {
    throw new InvalidPolicyError(`${where}: la celda tiene que ser un objeto`);
  }
  const extra = Object.keys(cell)
    .filter((key) => key !== 'default' && key !== 'locked' && key !== 'urgent')
    .sort();
  if (extra[0] !== undefined) {
    throw new InvalidPolicyError(`${where}: clave desconocida \`${extra[0]}\``);
  }
  if (!Object.hasOwn(cell, 'default')) {
    throw new InvalidPolicyError(`${where}: falta \`default\``);
  }
  const fallback = cell['default'];
  const locked = Object.hasOwn(cell, 'locked') ? cell['locked'] : false;
  if (typeof fallback !== 'boolean') {
    throw new InvalidPolicyError(`${where}: \`default\` tiene que ser un booleano`);
  }
  if (typeof locked !== 'boolean') {
    throw new InvalidPolicyError(`${where}: \`locked\` tiene que ser un booleano`);
  }
  const urgent = Object.hasOwn(cell, 'urgent') ? cell['urgent'] : false;
  if (typeof urgent !== 'boolean') {
    throw new InvalidPolicyError(`${where}: \`urgent\` tiene que ser un booleano`);
  }
  return Object.freeze({ default: fallback, locked, urgent });
}
