/**
 * Los puertos: lo que campanita necesita de afuera.
 *
 * BORRADOR. El spec v0 no cubre todavía la ingesta, la bandeja ni las entregas,
 * así que estos contratos pueden cambiar. Están acá para fijar la forma: el
 * núcleo no conoce la base, el canal ni la superficie que muestra la bandeja.
 *
 * Regla del proyecto: un puerto no se da por estable hasta tener dos adaptadores
 * reales (ver `docs/puertos.md`).
 */
import type { Change, Overrides } from './core/types.js';

/** Un aviso para UNA persona. `readAt` es lo único que cambia después de creado. */
export interface Notification {
  readonly id: string;
  readonly recipientId: string;
  readonly category: string;
  /** El tipo dentro del catálogo de quien integra (`invitations.received`). */
  readonly type: string;
  readonly title: string;
  /**
   * La pone quien guarda, nunca quien emite: la bandeja pagina por
   * `(createdAt, id)` y un reloj corrido insertaría filas en una página ya servida.
   */
  readonly createdAt: Date;
  readonly body?: string | null;
  readonly actionUrl?: string | null;
  /** Las variables con las que se armó el título, para volver a renderizar. */
  readonly payload?: Readonly<Record<string, unknown>>;
  /** Ámbito opcional (un tenant, un proyecto). `null` = aviso de cuenta, se ve siempre. */
  readonly scope?: string | null;
  /** Reenviar la misma clave no crea otro aviso. */
  readonly dedupeKey?: string | null;
  readonly readAt?: Date | null;
}

export interface FeedCursor {
  readonly createdAt: Date;
  readonly id: string;
}

export interface FeedQuery {
  readonly recipientId: string;
  /** Cuántos avisos como máximo. Lo decide quien muestra la bandeja. */
  readonly limit: number;
  /** El último aviso de la página anterior. */
  readonly cursor?: FeedCursor | null;
  readonly unreadOnly?: boolean;
  readonly category?: string | null;
  /** Filtra `scope == X o scope es null`. */
  readonly scope?: string | null;
}

export interface FeedPage {
  readonly items: readonly Notification[];
  readonly nextCursor: FeedCursor | null;
}

/**
 * Cualquier base. Todas las lecturas piden `recipientId`: es el único alcance
 * de seguridad, y por eso es un parámetro obligatorio.
 */
export interface NotificationStore {
  /** Guarda un lote de forma atómica. */
  addMany(notifications: readonly Notification[]): Promise<readonly Notification[]>;
  findByDedupeKey(recipientId: string, dedupeKey: string): Promise<Notification | null>;
  listFeed(query: FeedQuery): Promise<FeedPage>;
  /**
   * Cuántos sin leer. Con `cap`, deja de contar al llegar a ese número: una
   * interfaz que muestra "99+" no necesita el total exacto.
   */
  countUnread(recipientId: string, options?: { scope?: string | null; cap?: number }): Promise<number>;
  /** `true` sólo si hubo transición. Marcar dos veces no mueve la hora. */
  markRead(notificationId: string, recipientId: string, readAt: Date): Promise<boolean>;
  /** Marca lo anterior a `before`, para no tocar lo que llegó durante el click. */
  markAllRead(
    recipientId: string,
    options: { before: Date; scope?: string | null; category?: string | null },
  ): Promise<number>;
}

/** Guarda SÓLO los cambios del usuario. Sin filas = valores por defecto. */
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
 * Cualquier canal de entrega (email, web push, APNs…).
 *
 * Cada adaptador resuelve sus propios destinos a partir de `recipientId`.
 * `send` NUNCA lanza: un destino roto no puede impedir avisarle al resto.
 */
export interface DeliveryChannel {
  /** El identificador del canal en la política (`email`, `push`). */
  readonly id: string;
  send(notification: Notification): Promise<DeliveryResult>;
}
