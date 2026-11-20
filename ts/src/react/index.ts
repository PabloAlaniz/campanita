/**
 * `campanita/react`: la campanita en React, sin componentes. El dibujo es de
 * cada producto; esto conecta el estado.
 *
 *     const store = useMemo(() => createInboxStore(client, { scope: proyecto }), [proyecto]);
 *     const { items, unread, hasMore } = useInbox(store);
 */
import { useSyncExternalStore } from 'react';
import type { InboxSnapshot, InboxStore } from '../client/store.js';
import type { UnreadCountResponse } from '../http/wire.js';

/** El estado completo de la bandeja. Mientras el componente está montado, el store mira el contador. */
export function useInbox(store: InboxStore): InboxSnapshot {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

/** Sólo el contador, para el globito de la campana. */
export function useUnreadCount(store: InboxStore): UnreadCountResponse {
  return useInbox(store).unread;
}
