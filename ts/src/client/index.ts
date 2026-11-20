/** `campanita/client`: la bandeja por HTTP, del lado de la interfaz. Sin dependencias. */
export { createInboxClient, InboxRequestError } from './client.js';
export type { FeedOptions, InboxClient, InboxClientOptions, ScopeOptions } from './client.js';
export { createInboxStore } from './store.js';
export type { InboxSnapshot, InboxStore, InboxStoreOptions } from './store.js';
export type {
  FeedResponse,
  InboxErrorCode,
  InboxItem,
  PreferencesResponse,
  UnreadCountResponse,
} from '../http/wire.js';
export type { QuietHours } from '../core/quiet.js';
export type { Matrix, MatrixCell, Overrides } from '../core/types.js';
