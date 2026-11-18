/**
 * campanita: notificaciones con las mismas reglas en cualquier stack.
 *
 * El núcleo decide por qué canales sale un aviso y qué puede cambiar el
 * usuario. No conoce la base, el canal ni la superficie que muestra la
 * bandeja: eso son puertos.
 */
export { buildNotification, parseCatalog } from './core/catalog.js';
export { effectiveMatrix, resolveChannels, validatePatch } from './core/channels.js';
export { parsePolicy } from './core/policy.js';
export { renderTemplate } from './core/templates.js';
export {
  EventRejectedError,
  INBOX,
  InvalidCatalogError,
  InvalidPolicyError,
  InvalidTemplateError,
  PatchRejectedError,
  TemplateError,
} from './core/types.js';
export type {
  Catalog,
  Cell,
  Change,
  Draft,
  EventReason,
  Matrix,
  MatrixCell,
  Overrides,
  Policy,
  RejectionReason,
  TemplateReason,
  TypeSpec,
} from './core/types.js';
export { StoreError } from './core/inbox.js';
export type {
  AddResult,
  FeedCursor,
  FeedPage,
  FeedQuery,
  Notification,
  StoreReason,
} from './core/inbox.js';
export { notificationToWire, runStoreVector } from './conformance.js';
export { MemoryStore } from './stores/memory.js';
export type {
  DeliveryChannel,
  DeliveryOutcome,
  DeliveryResult,
  NotificationStore,
  PreferenceStore,
} from './ports.js';

/** La versión del spec que implementa este paquete. */
export const SPEC_VERSION = '0';
