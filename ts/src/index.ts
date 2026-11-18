/**
 * campanita: notificaciones con las mismas reglas en cualquier stack.
 *
 * El núcleo decide por qué canales sale un aviso y qué puede cambiar el
 * usuario. No conoce la base, el canal ni la superficie que muestra la
 * bandeja: eso son puertos.
 */
export { effectiveMatrix, resolveChannels, validatePatch } from './core/channels.js';
export { parsePolicy } from './core/policy.js';
export { INBOX, InvalidPolicyError, PatchRejectedError } from './core/types.js';
export type {
  Cell,
  Change,
  Matrix,
  MatrixCell,
  Overrides,
  Policy,
  RejectionReason,
} from './core/types.js';
export type {
  DeliveryChannel,
  DeliveryOutcome,
  DeliveryResult,
  FeedCursor,
  FeedPage,
  FeedQuery,
  Notification,
  NotificationStore,
  PreferenceStore,
} from './ports.js';

/** La versión del spec que implementa este paquete. */
export const SPEC_VERSION = '0';
