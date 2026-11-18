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
export { InvalidQuietHoursError, isQuiet, localMinute, parseQuietHours } from './core/quiet.js';
export type { QuietHours } from './core/quiet.js';
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
export { EmailChannel, formatSender, isValidAddress, renderEmail } from './channels/email.js';
export type {
  EmailChannelOptions,
  EmailMessage,
  EmailTransport,
  RenderedEmail,
} from './channels/email.js';
export { defaultBody, HttpEmailTransport, ResendTransport } from './channels/http.js';
export type { HttpEmailTransportOptions } from './channels/http.js';
export { InvalidChannelsError, Notifier } from './core/notify.js';
export type { NotifierOptions, NotifyResult } from './core/notify.js';
export { StoreError } from './core/inbox.js';
export type {
  AddResult,
  FeedCursor,
  FeedPage,
  FeedQuery,
  Notification,
  StoreReason,
} from './core/inbox.js';
export {
  notificationToWire,
  quietHoursToWire,
  runPreferenceVector,
  runQuietHoursVector,
  runStoreVector,
} from './conformance.js';
export { MemoryPreferenceStore, MemoryQuietHoursStore, MemoryStore } from './stores/memory.js';
export { D1PreferenceStore, D1QuietHoursStore, D1Store, d1Schema } from './stores/d1.js';
export type { D1Like, D1StatementLike } from './stores/d1.js';
export type {
  DeliveryChannel,
  DeliveryOutcome,
  DeliveryResult,
  NotificationStore,
  PreferenceStore,
  QuietHoursStore,
} from './ports.js';

/** La versión del spec que implementa este paquete. */
export const SPEC_VERSION = '0';
