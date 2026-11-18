/** No molestar: el horario en el que los canales de entrega no interrumpen a una persona. */

const TIME = /^[0-9]{2}:[0-9]{2}$/;
const ZONE = /^[A-Za-z0-9_+/-]{1,64}$/;
const KEYS = ['enabled', 'start', 'end', 'timezone'] as const;
const DAY = 24 * 60;

/** El horario de no molestar no cumple el spec. */
export class InvalidQuietHoursError extends Error {
  override readonly name = 'InvalidQuietHoursError';
}

/** El horario de no molestar de una persona. Se obtiene con `parseQuietHours`. */
export interface QuietHours {
  /** Apagado, el horario queda guardado pero no silencia nada. */
  readonly enabled: boolean;
  /** `HH:MM`, en la hora local de la persona. Entra en el horario. */
  readonly start: string;
  /** `HH:MM`. No entra en el horario. */
  readonly end: string;
  /** El nombre de la zona horaria de la persona. El núcleo no lo interpreta. */
  readonly timezone: string;
}

/** Valida un horario con forma de JSON. Lanza `InvalidQuietHoursError` con el primer problema. */
export function parseQuietHours(raw: unknown): QuietHours {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new InvalidQuietHoursError('el horario tiene que ser un objeto');
  }
  const object = raw as Record<string, unknown>;
  const extra = Object.keys(object)
    .filter((key) => !(KEYS as readonly string[]).includes(key))
    .sort();
  if (extra[0] !== undefined) throw new InvalidQuietHoursError(`clave desconocida \`${extra[0]}\``);
  for (const key of KEYS) {
    if (!Object.hasOwn(object, key)) throw new InvalidQuietHoursError(`falta \`${key}\``);
  }
  const { enabled, start, end, timezone } = object;
  if (typeof enabled !== 'boolean') {
    throw new InvalidQuietHoursError('`enabled` tiene que ser un booleano');
  }
  for (const [key, value] of [
    ['start', start],
    ['end', end],
  ] as const) {
    if (minuteOf(value) === null) {
      throw new InvalidQuietHoursError(`\`${key}\` tiene que ser una hora \`HH:MM\`, de 00:00 a 23:59`);
    }
  }
  if (typeof timezone !== 'string' || !ZONE.test(timezone)) {
    throw new InvalidQuietHoursError('`timezone` tiene que ser el nombre de una zona horaria');
  }
  return Object.freeze({
    enabled,
    start: start as string,
    end: end as string,
    timezone,
  });
}

/**
 * Si el minuto local `minute` (de 0 a 1439) cae dentro del horario.
 *
 * `start` entra y `end` no. Un horario con `start` mayor que `end` cruza la
 * medianoche; uno con los dos iguales está vacío.
 */
export function isQuiet(quietHours: QuietHours, minute: number): boolean {
  if (!Number.isInteger(minute) || minute < 0 || minute >= DAY) {
    throw new RangeError('el minuto tiene que ser un entero de 0 a 1439');
  }
  const start = minuteOf(quietHours.start);
  const end = minuteOf(quietHours.end);
  if (start === null || end === null) throw new TypeError('el horario no salió de parseQuietHours()');
  if (!quietHours.enabled || start === end) return false;
  if (start < end) return start <= minute && minute < end;
  return minute >= start || minute < end;
}

/**
 * El minuto del día de `moment` en la zona `timezone`, o `null` si la
 * plataforma no la conoce.
 *
 * Es lo único de no molestar que depende de la plataforma: usa su base de zonas
 * horarias, a través de `Intl`.
 */
export function localMinute(timezone: string, moment: Date): number | null {
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(moment);
  } catch {
    return null;
  }
  const part = (type: string): number => Number(parts.find((p) => p.type === type)?.value);
  const minute = part('hour') * 60 + part('minute');
  return Number.isInteger(minute) && minute >= 0 && minute < DAY ? minute : null;
}

function minuteOf(text: unknown): number | null {
  if (typeof text !== 'string' || !TIME.test(text)) return null;
  const hours = Number(text.slice(0, 2));
  const minutes = Number(text.slice(3));
  return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
}
