/**
 * Los puertos: lo que campanita necesita de afuera.
 *
 * El núcleo no conoce la base, el canal ni la superficie que muestra la bandeja.
 *
 * - `NotificationStore` y `PreferenceStore` están especificados: su
 *   comportamiento es el de las secuencias de `spec/vectors/bandeja/` y
 *   `spec/vectors/preferencias-guardadas/`, y `runStoreVector` y
 *   `runPreferenceVector` las corren contra cualquier adaptador.
 * - `DeliveryChannel` se usa en el recorrido completo, que está especificado
 *   (`spec/vectors/recorrido/`), pero todavía no tiene adaptadores reales.
 */
import type { AddResult, FeedPage, FeedQuery, Notification } from './core/inbox.js';
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

/** `expired` = el destino ya no existe (una suscripción vencida) y hay que olvidarlo. */
export type DeliveryOutcome = 'sent' | 'expired' | 'failed';

export interface DeliveryResult {
  readonly outcome: DeliveryOutcome;
  /** Identificador del mensaje en el proveedor, si lo dio. */
  readonly providerMessageId?: string | null;
  readonly error?: string | null;
}

/**
 * Cualquier canal de entrega fuera de la bandeja.
 *
 * Cada adaptador resuelve sus propios destinos a partir de `recipientId`: a qué
 * dirección o a qué dispositivos va un aviso es asunto suyo.
 *
 * `send` no debería rechazar: un destino roto se informa como `failed`, y uno
 * que ya no existe, como `expired`. Si igual rechaza, `Notifier` lo anota como
 * `failed` y sigue con los demás canales.
 *
 * El recorrido está especificado (`spec/vectors/recorrido/`). Lo que todavía no
 * hay son adaptadores reales, así que el puerto puede ajustarse.
 */
export interface DeliveryChannel {
  /** El identificador del canal en la política. */
  readonly id: string;
  send(notification: Notification): Promise<DeliveryResult>;
}
