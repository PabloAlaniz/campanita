# campanita (TypeScript)

El núcleo de [campanita](https://github.com/PabloAlaniz/campanita) en TypeScript: decide por qué
canales sale un aviso, qué puede cambiar el usuario y cómo se arma un aviso. Sin dependencias y
sin APIs de Node, así que no está atado a un runtime; hoy se prueba en Node.

```bash
npm install campanita
```

> **Versión temprana.** Implementa la versión 0 del spec. Hasta la 1.0, la API puede cambiar
> entre versiones menores; el [CHANGELOG](https://github.com/PabloAlaniz/campanita/blob/main/CHANGELOG.md)
> dice qué cambió.

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

Un almacén guarda los avisos y contesta la bandeja. El paquete trae uno en memoria y uno para
Cloudflare D1:

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

### Cloudflare D1

`D1Store`, `D1PreferenceStore` y `D1QuietHoursStore` guardan en una base D1. Reciben el binding
del Worker tal cual:

```ts
import { D1PreferenceStore, D1QuietHoursStore, D1Store, d1Schema } from 'campanita';

const store = new D1Store(env.DB);
const preferences = new D1PreferenceStore(env.DB);
const quietHours = new D1QuietHoursStore(env.DB);
```

Las tablas se crean una vez. `d1Schema()` devuelve las sentencias para copiar a una migración, o
`await store.createSchema()` las corre directamente. Los nombres de las tablas se pueden cambiar:
`new D1Store(env.DB, { table: 'avisos' })`.

Que dos pedidos simultáneos con la misma `dedupeKey` dejen un solo aviso lo garantiza un índice
único y una sola sentencia de inserción. Los almacenes de D1 se prueban con las mismas secuencias
del spec, contra la base D1 del runtime local de Workers.

Para otra base, escribí un adaptador que cumpla `NotificationStore` y probalo con
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

## El recorrido completo

`Notifier` junta todo: arma el aviso, lo guarda, lee las preferencias de esa persona y lo entrega
por los canales que corresponden.

```ts
import { Notifier, type DeliveryChannel } from 'campanita';

const consola: DeliveryChannel = {
  id: 'email', // el identificador que usa la política
  async send(notification) {
    console.log(`[${notification.recipientId}] ${notification.title}`);
    return { outcome: 'sent' }; // o 'expired', o 'failed'
  },
};

const notifier = new Notifier({ policy, catalog, store, preferences, channels: [consola] });

const result = await notifier.notify({ type: 'comments.new', recipient_id: 'user-7', payload: {} });
result.created; // false si la dedupe_key ya existía: no se vuelve a entregar
result.deliveries; // Map { 'email' => { outcome: 'sent' } }
```

Primero se guarda y después se entrega: un canal que falla, o que rechaza, no deshace el aviso ni
impide intentar los demás. Un canal es cualquier objeto con `id` y `send`.

### Avisos para un grupo

Un tipo con `audience: 'group'` le habla a un grupo (un equipo, un proyecto) y no a una persona.
El `recipient_id` es el del grupo: el aviso sale **una sola vez**, por los canales que eligió el
grupo y a sus destinos (un canal de Slack compartido, por ejemplo). A cada miembro le queda una
copia en su bandeja, que no se entrega por ningún canal.

```ts
const catalog = parseCatalog(
  {
    'report.ready': { category: 'reports', title: 'Informe listo: {name}', audience: 'group' },
    'report.mine': { category: 'reports', title: 'Tu informe está listo: {name}' }, // audience: 'person'
  },
  policy,
);

await notifier.notify(
  { type: 'report.ready', recipient_id: 'team-3', scope: 'project-9', payload: { name: 'Marzo' } },
  { members: [{ recipient_id: 'user-7', id: 'n-1' }, { recipient_id: 'user-8', id: 'n-2' }] },
);
```

- **Quiénes forman el grupo lo decide quien integra**: campanita no guarda grupos.
- **Las preferencias y el horario del grupo** son los guardados con su `recipient_id`.
- **Un mismo hecho para una persona y para un grupo son dos tipos**: quien emite elige cuál
  manda, o los dos.
- **Un tipo de persona no acepta miembros**: el aviso se rechaza con `invalid_field` (`members`)
  y no se guarda nada.

## No molestar

Cada persona puede tener un horario en el que los canales no la interrumpen. El aviso se guarda
igual; lo que no sale queda anotado como `silenced`.

```ts
import { MemoryQuietHoursStore, parseQuietHours } from 'campanita';

const quietHours = new MemoryQuietHoursStore();
await quietHours.setQuietHours(
  'user-7',
  parseQuietHours({ enabled: true, start: '22:00', end: '08:00', timezone: 'America/Lima' }),
);

const notifier = new Notifier({ policy, catalog, store, preferences, channels, quietHours });
const result = await notifier.notify(evento); // a las 23:30 de esa persona
result.deliveries; // Map { 'email' => { outcome: 'silenced' } }
```

Una celda de la política con `urgent: true` se entrega igual:
`{ default: true, locked: true, urgent: true }`. La hora local sale de `Intl`; si la plataforma no
conoce la zona, se entrega. Lo silenciado no se manda después: eso lo decide quien integra.

## Email

`EmailChannel` busca la dirección de la persona, arma el mensaje y se lo pasa a un transporte. El
transporte se cambia sin tocar el resto:

```ts
import { EmailChannel, HttpEmailTransport, ResendTransport } from 'campanita';

const email = new EmailChannel({
  transport: new ResendTransport('re_…'),
  sender: 'avisos@example.com',
  senderName: 'Avisos',
  resolveAddress: async (recipientId) => buscarDireccion(recipientId), // null si no tiene
});

// El mismo canal, con cualquier servicio de email que tenga una API HTTP:
new HttpEmailTransport('https://mail.example.com/v1/send', {
  headers: { 'X-Api-Key': '…' },
  buildBody: (m) => ({ to: m.to, subject: m.subject, body: m.text }),
});

// Un servicio que contesta 200 también cuando no mandó el mensaje, y lo dice en el cuerpo:
new HttpEmailTransport('https://mail.example.com/v1/send', {
  headers: { 'X-Api-Key': '…' },
  // El identificador del aviso, para que un reintento no mande el mensaje dos veces.
  buildBody: (m) => ({ to: m.to, subject: m.subject, body: m.text, idempotency_key: m.idempotencyKey }),
  readResult: (r) => {
    const reply = r as { ok: boolean; id?: string; code?: string };
    if (reply.ok) return { outcome: 'sent', providerMessageId: reply.id ?? null };
    if (reply.code === 'unsubscribed') return { outcome: 'skipped', error: reply.code };
    return { outcome: 'failed', error: reply.code ?? null };
  },
});
```

- **Sin dirección no es una falla.** Si la persona no tiene dirección, o no es válida, la entrega
  queda como `skipped` y el aviso sigue en la bandeja.
- **El mensaje por omisión** lleva el título como asunto, y el cuerpo y el enlace como texto. Se
  cambia con `render`; el asunto nunca sale con saltos de línea.
- **Los transportes usan `fetch`**, así que corren donde corra el paquete. No siguen
  redirecciones y no repiten credenciales en los errores. Se les puede pasar otro `fetch`.
- **Un 2xx es `sent`**, salvo que se pase `readResult`: entonces el resultado lo dice el cuerpo
  (`sent`, `failed` o `skipped`). Un cuerpo que no se entiende es `failed`.
- **SMTP todavía no está en TypeScript**; sí en el paquete de Python.

Los transportes están probados con un `fetch` de prueba. Ninguno se probó contra un proveedor
real: conviene hacer un envío de prueba antes de confiar en la configuración.

## Slack

`SlackChannel` avisa en el canal de Slack que conectó cada persona. La conexión es por OAuth: la
persona elige el canal en la pantalla de Slack, entre los que puede usar, y nunca ve ni pega un
token. Lo que queda es la URL de un webhook, que guarda quien integra.

```ts
import { exchangeSlackCode, SlackChannel, slackAuthorizeUrl } from 'campanita';

// 1. Mandar a la persona a Slack. `state` es tuyo: atado a su sesión e imposible de adivinar.
const url = slackAuthorizeUrl({ clientId, redirectUri, state });

// 2. Slack la devuelve a `redirectUri` con `code` y `state`. Comprobá `state` y cambiá el código:
const connection = await exchangeSlackCode({ clientId, clientSecret, code, redirectUri });
await guardarConexion(recipientId, connection); // webhookUrl es un secreto; channel es «#avisos»

// 3. El canal pide la URL cuando hay algo que entregar.
const slack = new SlackChannel({
  resolveWebhook: async (recipientId) => buscarWebhook(recipientId), // null si no conectó
});

// O, con un canal distinto por ámbito (un proyecto, un equipo), a partir del aviso entero:
const porAmbito = new SlackChannel({
  resolveWebhookFor: async (n) => buscarWebhook(n.recipientId, n.scope),
});
```

- **La app de Slack es tuya**: se crea en Slack con el permiso `incoming-webhook` y la dirección
  de vuelta. Para conectarla a espacios ajenos tiene que estar habilitada la distribución.
- **Quién puede publicar dónde lo decide Slack.** La pantalla de conexión sólo ofrece los canales
  que esa persona puede usar; campanita no lista canales ni guarda tokens.
- **Sin conexión no es una falla**: la entrega queda como `skipped`.
- **`expired` es para reconectar.** Si Slack contesta que el webhook se quitó o que el canal se
  archivó, la entrega queda como `expired`: conviene borrar la conexión y avisarle a la persona.
- **El mensaje por omisión** lleva el título en negrita, el cuerpo y el enlace si es una URL
  `http` o `https`. El texto del aviso sale escapado: no arma menciones ni enlaces. Se cambia con
  `render`, que devuelve el cuerpo JSON del webhook.
- **Sólo se publica en `https://hooks.slack.com/services/…`.** Otra URL no se usa, y ningún error
  repite la del webhook.
- **Desconectar es borrar la URL guardada.** El webhook sigue existiendo en Slack hasta que la
  persona lo quita desde ahí (`configurationUrl`).

Está probado con un `fetch` de prueba, no contra Slack: conviene hacer una conexión real antes de
confiar en la configuración.

## Desarrollo

Desde la raíz del repo:

```bash
pnpm install
pnpm test
pnpm typecheck
pnpm build
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de Python.
