/**
 * La bandeja. Está en la política y en la matriz de preferencias, pero no es un
 * canal de entrega: la notificación guardada ES la entrega in-app.
 */
export const INBOX = 'inapp';

/** La política de un canal para una categoría. */
export interface Cell {
  /** Qué pasa si el usuario nunca tocó esta celda. */
  readonly default: boolean;
  /** El usuario no puede cambiarla. */
  readonly locked: boolean;
}

declare const validated: unique symbol;

/**
 * Una política ya validada e inmutable: categoría → canal → celda.
 *
 * Se obtiene con `parsePolicy`. Tenerla en la mano garantiza las reglas del
 * spec, así que el resto del núcleo no las vuelve a revisar.
 *
 * Los objetos no tienen prototipo: `policy['constructor']` es `undefined` salvo
 * que la política tenga de verdad una categoría con ese nombre.
 */
export type Policy = Readonly<Record<string, Readonly<Record<string, Cell>>>> & {
  readonly [validated]: true;
};

/** Las celdas que el usuario cambió: categoría → canal → habilitado. */
export type Overrides = Readonly<Record<string, Readonly<Record<string, boolean>>>>;

/** Una celda de la matriz efectiva, lista para mostrar. */
export interface MatrixCell {
  readonly enabled: boolean;
  /** No se puede tocar: la política la bloquea o el canal no está disponible. */
  readonly locked: boolean;
}

export type Matrix = Record<string, Record<string, MatrixCell>>;

/** Un cambio de preferencias ya validado, listo para guardar. */
export interface Change {
  readonly category: string;
  readonly channel: string;
  readonly enabled: boolean;
}

/** Por qué se rechazó un cambio de preferencias. Son los literales del spec. */
export type RejectionReason =
  | 'unknown_category'
  | 'unknown_channel'
  | 'invalid_value'
  | 'locked'
  | 'unavailable';

/** La política no cumple el spec. Se detecta al cargarla, no al resolver. */
export class InvalidPolicyError extends Error {
  override readonly name = 'InvalidPolicyError';
}

const MOTIVOS: Record<RejectionReason, string> = {
  unknown_category: 'la categoría no está en la política',
  unknown_channel: 'el canal no está en esa categoría',
  invalid_value: 'el valor no es un booleano',
  locked: 'la celda está bloqueada',
  unavailable: 'el canal no está disponible',
};

/** El cambio de preferencias se rechazó entero. No hay que guardar nada. */
export class PatchRejectedError extends Error {
  override readonly name = 'PatchRejectedError';

  constructor(
    readonly category: string,
    /** `null` cuando lo que no existe es la categoría. */
    readonly channel: string | null,
    readonly reason: RejectionReason,
  ) {
    const where = channel === null ? category : `${category}.${channel}`;
    super(`cambio de preferencias rechazado en ${where}: ${MOTIVOS[reason]}`);
  }
}
