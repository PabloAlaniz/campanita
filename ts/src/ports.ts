/**
 * Los puertos: lo que campanita necesita de afuera.
 *
 * El núcleo no conoce la base, el canal ni la superficie que muestra la bandeja.
 *
 * - `NotificationStore`, `PreferenceStore` y `QuietHoursStore` están
 *   especificados: su comportamiento es el de las secuencias de
 *   `spec/vectors/bandeja/`, `spec/vectors/preferencias-guardadas/` y
 *   `spec/vectors/silencio-guardado/`, y `runStoreVector`, `runPreferenceVector`
 *   y `runQuietHoursVector` las corren contra cualquier adaptador.
 * - `DeliveryChannel` se usa en el recorrido completo, que está especificado
 *   (`spec/vectors/recorrido/`). Los canales incluidos son `EmailChannel` y `SlackChannel`.
 */
import type { AddResult, FeedPage, FeedQuery, Notification } from './core/inbox.js';
import type { QuietHours } from './core/quiet.js';
import type { Change, Draft, Overrides } from './core/types.js';

/**
 * Cualquier base. Todas las lecturas piden `recipientId`: es el único alcance
 * de seguridad, y por eso es un parámetro obligatorio.
 */
export interface NotificationStore {
  /**
   * Guarda el aviso, salvo que su clave ya exista para esa persona.
   *
   * Revisar la clave y guardar tiene que ser atómico: dos pedidos a la vez con
   * la misma clave dejan un solo aviso. Rechaza con `StoreError('duplicate_id')`.
   */
  add(draft: Draft, options: { id: string; createdAt: Date }): Promise<AddResult>;
  /** La bandeja, del más nuevo al más viejo. Rechaza con `StoreError('invalid_limit')`. */
  listFeed(query: FeedQuery): Promise<FeedPage>;
  /** Cuántos sin leer. Con `cap`, deja de contar al llegar a ese número. */
  countUnread(recipientId: string, options?: { scope?: string | null; cap?: number | null }): Promise<number>;
  /** `true` sólo si hubo transición. Marcar dos veces no mueve la hora. */
  markRead(id: string, recipientId: string, readAt: Date): Promise<boolean>;
  /**
   * Marca lo creado antes de `before`, para no tocar lo que llegó después de lo
   * que la persona vio. Devuelve cuántos cambió.
   */
  markAllRead(
    recipientId: string,
    options: { before: Date; readAt: Date; scope?: string | null; category?: string | null },
  ): Promise<number>;
}

/**
 * Lo que cambió cada persona. Guarda SÓLO los cambios: sin nada guardado, valen
 * los valores de la política.
 *
 * El almacén no conoce la política. Qué se puede cambiar lo decide
 * `validatePatch` antes de guardar.
 */
export interface PreferenceStore {
  /** Lo guardado para esa persona en ese ámbito. Los ámbitos no se heredan. */
  getOverrides(recipientId: string, options?: { scope?: string | null }): Promise<Overrides>;
  /** Guarda los cambios, todos o ninguno. Si una celda se repite, vale la última. */
  saveChanges(recipientId: string, changes: readonly Change[], options?: { scope?: string | null }): Promise<void>;
}

/**
 * El horario de no molestar de cada persona. Es de la persona, no de un ámbito.
 *
 * El almacén guarda el horario que le pasen: lo valida `parseQuietHours` antes.
 * Su comportamiento es el de `spec/vectors/silencio-guardado/`.
 */
export interface QuietHoursStore {
  /** El horario de esa persona, o `null` si nunca definió uno. */
  getQuietHours(recipientId: string): Promise<QuietHours | null>;
  /** Lo guarda, reemplazando el que hubiera. */
  setQuietHours(recipientId: string, quietHours: QuietHours): Promise<void>;
}

/**
 * - `sent`: el proveedor aceptó el aviso.
 * - `expired`: el destino ya no existe y el adaptador lo tiene que olvidar.
 * - `failed`: no se pudo entregar.
 * - `skipped`: no había a dónde entregar. Reintentar no sirve.
 * - `silenced`: no lo contesta un canal, lo anota `Notifier`: el canal no se
 *   intentó porque la persona está en su horario de no molestar.
 */
export type DeliveryOutcome = 'sent' | 'expired' | 'failed' | 'skipped' | 'silenced';

export interface DeliveryResult {
  readonly outcome: DeliveryOutcome;
  /** Identificador del mensaje en el proveedor, si lo dio. */
  readonly providerMessageId?: string | null;
  readonly error?: string | null;
}

/**
 * Cualquier canal de entrega fuera de la bandeja.
 *
 * Cada adaptador resuelve sus propios destinos a partir del aviso: a qué
 * dirección o a qué dispositivos va es asunto suyo. El destinatario y el ámbito
 * viajan en el aviso, así que un destino puede cambiar de un ámbito a otro.
 *
 * `send` no debería rechazar: informa uno de los resultados de
 * `DeliveryOutcome`. Si igual rechaza, `Notifier` lo anota como `failed` y sigue
 * con los demás canales.
 *
 * El recorrido está especificado (`spec/vectors/recorrido/`). El primer canal
 * incluido es `EmailChannel`; el segundo, `SlackChannel`.
 */
export interface DeliveryChannel {
  /** El identificador del canal en la política. */
  readonly id: string;
  send(notification: Notification): Promise<DeliveryResult>;
}
