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

Los puertos (`NotificationStore`, `PreferenceStore`, `DeliveryChannel`) son un borrador: fijan la
forma que van a tener la base, los canales de entrega y las preferencias, pero el spec todavía
no los cubre.

## Desarrollo

Desde la raíz del repo:

```bash
pnpm install
pnpm test
pnpm typecheck
pnpm build
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de Python.
