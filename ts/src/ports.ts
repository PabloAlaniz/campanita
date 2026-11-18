/**
 * Los puertos: lo que campanita necesita de afuera.
 *
 * El núcleo no conoce la base, el canal ni la superficie que muestra la bandeja.
 *
 * - `NotificationStore` está especificado: su comportamiento es el de las
 *   secuencias de `spec/vectors/bandeja/`, y `runStoreVector` las corre contra
 *   cualquier adaptador.
 * - `PreferenceStore` y `DeliveryChannel` son un BORRADOR: el spec todavía no
 *   los cubre y pueden cambiar.
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

/** BORRADOR. Guarda SÓLO los cambios del usuario. Sin filas = valores por defecto. */
export interface PreferenceStore {
  getOverrides(recipientId: string, scope?: string | null): Promise<Overrides>;
  saveChanges(recipientId: string, changes: readonly Change[], scope?: string | null): Promise<void>;
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
 * BORRADOR. Cualquier canal de entrega.
 *
 * Cada adaptador resuelve sus propios destinos a partir de `recipientId`.
 * `send` NUNCA lanza: un destino roto no puede impedir avisarle al resto.
 */
export interface DeliveryChannel {
  /** El identificador del canal en la política. */
  readonly id: string;
  send(notification: Notification): Promise<DeliveryResult>;
}
