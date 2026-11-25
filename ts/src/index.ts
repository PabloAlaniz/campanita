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
  Audience,
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
export { escapeSlackText, isSlackWebhookUrl, renderSlack, SlackChannel } from './channels/slack.js';
export type { RenderedSlack, SlackChannelOptions } from './channels/slack.js';
export {
  exchangeSlackCode,
  parseSlackOAuthResponse,
  slackAuthorizeUrl,
  SlackOAuthError,
} from './channels/slack-oauth.js';
export type { SlackConnection } from './channels/slack-oauth.js';
export { PushChannel, renderPush, summarizePush } from './channels/push.js';
export type { PushChannelOptions, PushDevice, PushMessage, PushTransport } from './channels/push.js';
export { ApnsTransport } from './channels/apns.js';
export type { ApnsTarget, ApnsTransportOptions } from './channels/apns.js';
export { WebPushTransport } from './channels/webpush.js';
export type { WebPushTarget, WebPushTransportOptions } from './channels/webpush.js';
export { InvalidChannelsError, Notifier } from './core/notify.js';
export type { Member } from './core/catalog.js';
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
// También en `campanita/testing`, junto con las reglas compartidas de los almacenes.
export {
  notificationToWire,
  quietHoursToWire,
  recipientFromVectorRequest,
  runHttpVector,
  runPreferenceVector,
  runQuietHoursVector,
  runStoreVector,
} from './conformance.js';
export type { HttpVectorSetup } from './conformance.js';
export { MemoryPreferenceStore, MemoryQuietHoursStore, MemoryStore } from './stores/memory.js';
export { D1PreferenceStore, D1QuietHoursStore, D1Store, d1Schema } from './stores/d1.js';
export type { D1Like, D1StatementLike } from './stores/d1.js';
export {
  fromPostgresJs,
  PostgresPreferenceStore,
  PostgresQuietHoursStore,
  PostgresStore,
  postgresSchema,
} from './stores/postgres.js';
export type { PostgresJsLike, PostgresLike } from './stores/postgres.js';
export type {
  ChannelAvailability,
  DeliveryChannel,
  DeliveryOutcome,
  DeliveryResult,
  InboxReader,
  NotificationStore,
  NotificationWriter,
  PreferenceReader,
  PreferenceStore,
  QuietHoursReader,
  QuietHoursStore,
} from './ports.js';

/** La versión del spec que implementa este paquete. */
export const SPEC_VERSION = '0';
