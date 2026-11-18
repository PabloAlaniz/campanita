# campanita (TypeScript)

El núcleo de [campanita](https://github.com/PabloAlaniz/campanita) en TypeScript: decide por qué
canales sale un aviso, qué puede cambiar el usuario y cómo se arma un aviso. Sin dependencias y
sin APIs de Node, así que no está atado a un runtime; hoy se prueba en Node.

> **Borrador.** Implementa la versión 0 del spec, que puede cambiar. Todavía no hay adaptadores
> de base ni de canal.

```ts
import { parsePolicy, resolveChannels, effectiveMatrix, validatePatch } from 'campanita';

const policy = parsePolicy({
  security: {
    inapp: { default: true, locked: true },
    email: { default: true, locked: true },
  },
  activity: {
    inapp: { default: true, locked: true },
    email: { default: false },
    push: { default: false },
  },
});

const disponibles = ['email'];                       // lo que esta instalación puede entregar
const overrides = { activity: { email: true } };     // lo que cambió la persona

resolveChannels(policy, 'activity', overrides, disponibles); // ['email']
resolveChannels(policy, 'security', { security: { email: false } }, disponibles);
// ['email']: seguridad está bloqueada, el override no la apaga

effectiveMatrix(policy, overrides, disponibles)['activity']?.['push'];
// { enabled: false, locked: true }: push todavía no está disponible

validatePatch(policy, { activity: { email: false } }, disponibles);
// [{ category: 'activity', channel: 'email', enabled: false }]
validatePatch(policy, { security: { email: false } }, disponibles);
// PatchRejectedError: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

Para armar un aviso a partir de un evento:

```ts
import { parseCatalog, buildNotification } from 'campanita';

const catalog = parseCatalog(
  {
    'comments.new': {
      category: 'activity',
      title: '{author} comentó en {document}',
      body: '{excerpt}',
    },
  },
  policy,
);

const draft = buildNotification(catalog, {
  type: 'comments.new',
  recipient_id: 'user-7',
  payload: { author: 'Ana', document: 'Presupuesto', excerpt: '¿Va con IVA?' },
  dedupe_key: 'comment-381',
});
draft.title; // 'Ana comentó en Presupuesto'
draft.category; // 'activity'

buildNotification(catalog, { type: 'comments.new', recipient_id: 'user-7', payload: {} });
// EventRejectedError: evento rechazado: falta una variable (author)
```

El evento usa los nombres que viajan (`recipient_id`, `dedupe_key`), porque suele llegar de otro
servicio; el aviso armado usa los de TypeScript (`recipientId`, `dedupeKey`).

Las plantillas sólo reemplazan `{variables}` por texto o enteros. No hay formatos ni expresiones:
lo que hacen es igual en todos los lenguajes.

## La bandeja

Un almacén guarda los avisos y contesta la bandeja. El paquete trae uno en memoria:

```ts
import { MemoryStore } from 'campanita';

const store = new MemoryStore();
const added = await store.add(draft, { id: crypto.randomUUID(), createdAt: new Date() });
added.created; // false si esa persona ya tenía un aviso con la misma dedupeKey

const page = await store.listFeed({ recipientId: 'user-7', limit: 20 });
if (page.nextCursor) {
  await store.listFeed({ recipientId: 'user-7', limit: 20, cursor: page.nextCursor });
}

await store.countUnread('user-7', { cap: 100 });
await store.markRead(page.items[0]!.id, 'user-7', new Date());
await store.markAllRead('user-7', { before: page.items[0]!.createdAt, readAt: new Date() });
```

Para una base de verdad, escribí un adaptador que cumpla `NotificationStore` y probalo con
`runStoreVector`: ver [`docs/puertos.md`](../docs/puertos.md).

## Las preferencias guardadas

Lo que cambió cada persona va a un almacén de preferencias. El recorrido completo de una pantalla
de preferencias es validar, guardar y volver a leer:

```ts
import { MemoryPreferenceStore, effectiveMatrix, validatePatch } from 'campanita';

const preferences = new MemoryPreferenceStore();

const changes = validatePatch(policy, { activity: { email: true } }, disponibles); // rechaza lo bloqueado
await preferences.saveChanges('user-7', changes);

const saved = await preferences.getOverrides('user-7');
effectiveMatrix(policy, saved, disponibles); // lo que muestra la pantalla
resolveChannels(policy, 'activity', saved, disponibles); // por dónde sale el próximo aviso
```

El puerto de canales de entrega (`DeliveryChannel`) es un borrador: el spec todavía no lo cubre.

## Desarrollo

Desde la raíz del repo:

```bash
pnpm install
pnpm test
pnpm typecheck
pnpm build
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de Python.
