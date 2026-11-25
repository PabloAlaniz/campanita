/**
 * La bandeja. Está en la política y en la matriz de preferencias, pero no es un
 * canal de entrega: la notificación guardada ES la entrega in-app.
 */
export const INBOX = 'inapp';

/** Un identificador de categoría o de canal. */
export const ID = /^[a-z][a-z0-9_-]{0,63}$/;

/** La política de un canal para una categoría. */
export interface Cell {
  /** Qué pasa si el usuario nunca tocó esta celda. */
  readonly default: boolean;
  /** El usuario no puede cambiarla. */
  readonly locked: boolean;
  /** Se entrega aunque la persona esté en su horario de no molestar. */
  readonly urgent: boolean;
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

/** La plantilla está mal escrita: una llave suelta o un nombre de variable inválido. */
export class InvalidTemplateError extends Error {
  override readonly name = 'InvalidTemplateError';
}

/** Por qué no se pudo armar un texto. */
export type TemplateReason = 'missing_variable' | 'invalid_variable';

/** Falta una variable o su valor no se puede mostrar. */
export class TemplateError extends Error {
  override readonly name = 'TemplateError';

  constructor(
    readonly reason: TemplateReason,
    readonly variable: string,
  ) {
    const motivo = reason === 'missing_variable' ? 'falta la variable' : 'no se puede mostrar el valor de';
    super(`${motivo} \`${variable}\``);
  }
}

/** El catálogo no cumple el spec. Se detecta al cargarlo. */
export class InvalidCatalogError extends Error {
  override readonly name = 'InvalidCatalogError';
}

/** Por qué se rechazó un evento. Son los literales del spec. */
export type EventReason =
  | 'unknown_type'
  | 'invalid_field'
  | 'missing_variable'
  | 'invalid_variable'
  | 'empty_title';

const MOTIVOS_EVENTO: Record<EventReason, string> = {
  unknown_type: 'el tipo no está en el catálogo',
  invalid_field: 'campo inválido o desconocido',
  missing_variable: 'falta una variable',
  invalid_variable: 'el valor de una variable no se puede mostrar',
  empty_title: 'el título queda vacío',
};

/** El evento no se puede convertir en un aviso. No hay que guardar nada. */
export class EventRejectedError extends Error {
  override readonly name = 'EventRejectedError';

  constructor(
    readonly reason: EventReason,
    /** El tipo, el campo o la variable del problema, según el motivo. */
    readonly detail: string | null,
  ) {
    super(`evento rechazado: ${MOTIVOS_EVENTO[reason]}${detail === null ? '' : ` (${detail})`}`);
  }
}

/**
 * A quién le habla un tipo de aviso. `person`, a una persona. `group`, a un
 * grupo: sale una sola vez, por los canales del grupo, y a los miembros les
 * queda una copia en la bandeja.
 */
export type Audience = 'person' | 'group';

/** Un tipo de aviso: a qué categoría pertenece, a quién le habla y cómo se redacta. */
export interface TypeSpec {
  readonly category: string;
  readonly title: string;
  readonly body: string | null;
  readonly audience: Audience;
}

/**
 * Un catálogo ya validado contra una política. Se obtiene con `parseCatalog`.
 * Igual que la política, no tiene prototipo ni hereda propiedades.
 */
export type Catalog = Readonly<Record<string, TypeSpec>> & {
  readonly [validated]: true;
};

/**
 * Un aviso armado y validado, antes de guardarse.
 *
 * No tiene `id` ni fecha: los pone quien guarda.
 */
export interface Draft {
  readonly recipientId: string;
  readonly type: string;
  readonly category: string;
  readonly title: string;
  readonly body: string | null;
  readonly actionUrl: string | null;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly scope: string | null;
  readonly dedupeKey: string | null;
}
