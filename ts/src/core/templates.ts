/**
 * Plantillas: texto con variables `{nombre}`.
 *
 * Es a propósito lo mínimo: sin formatos, sin expresiones, sin acceso a
 * propiedades. Lo que una plantilla puede hacer es igual en todos los lenguajes.
 */
import { InvalidTemplateError, TemplateError } from './types.js';

const VAR = /^[a-z][a-z0-9_]{0,63}$/;

/** Una plantilla ya separada: tramos de texto y nombres de variable. */
export type Parsed = ReadonlyArray<{ readonly variable: boolean; readonly value: string }>;

/** Separa la plantilla en tramos. Lanza `InvalidTemplateError`. */
export function parseTemplate(template: unknown): Parsed {
  if (typeof template !== 'string') {
    throw new InvalidTemplateError('la plantilla tiene que ser texto');
  }
  const parts: Array<{ variable: boolean; value: string }> = [];
  let text = '';
  let i = 0;
  while (i < template.length) {
    const char = template[i];
    if (char === '{') {
      if (template[i + 1] === '{') {
        text += '{';
        i += 2;
        continue;
      }
      const close = template.indexOf('}', i);
      const name = close === -1 ? '' : template.slice(i + 1, close);
      if (close === -1 || !VAR.test(name)) {
        throw new InvalidTemplateError(
          `llave sin cerrar o nombre de variable inválido en la posición ${i}`,
        );
      }
      if (text !== '') {
        parts.push({ variable: false, value: text });
        text = '';
      }
      parts.push({ variable: true, value: name });
      i = close + 1;
    } else if (char === '}') {
      if (template[i + 1] !== '}') {
        throw new InvalidTemplateError(`llave de cierre suelta en la posición ${i}`);
      }
      text += '}';
      i += 2;
    } else {
      text += char;
      i += 1;
    }
  }
  if (text !== '') parts.push({ variable: false, value: text });
  return parts;
}

export function renderParsed(parts: Parsed, payload: Readonly<Record<string, unknown>>): string {
  let out = '';
  for (const part of parts) {
    if (!part.variable) {
      out += part.value;
    } else if (!Object.hasOwn(payload, part.value)) {
      // Sólo las propiedades propias: `{constructor}` no existe en un payload vacío.
      throw new TemplateError('missing_variable', part.value);
    } else {
      out += show(payload[part.value], part.value);
    }
  }
  return out;
}

/**
 * Arma el texto. La plantilla se revisa entera antes de mirar el payload.
 *
 * Lanza `InvalidTemplateError` si la plantilla está mal escrita y
 * `TemplateError` si falta una variable o su valor no se puede mostrar.
 */
export function renderTemplate(template: string, payload: Readonly<Record<string, unknown>>): string {
  return renderParsed(parseTemplate(template), payload);
}

function show(value: unknown, name: string): string {
  if (typeof value === 'string') return value;
  // Enteros en el rango que cualquier lenguaje representa exacto. `-0` se muestra "0".
  if (typeof value === 'number' && Number.isSafeInteger(value)) return String(value);
  throw new TemplateError('invalid_variable', name);
}
