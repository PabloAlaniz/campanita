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

Un almacén guarda los avisos y contesta la bandeja. El paquete trae uno en memoria, uno para
Cloudflare D1 y uno para Postgres:

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

### Postgres

`PostgresStore`, `PostgresPreferenceStore` y `PostgresQuietHoursStore` guardan en Postgres. El
paquete no trae ni pide un driver: reciben cualquier objeto con `query(text, params)` que devuelva
`{ rows, rowCount }`. El `Pool` y el `Client` de [`pg`](https://node-postgres.com) y de
`@neondatabase/serverless` lo cumplen tal cual, y el driver por HTTP de Neon también, con
`neon(url, { fullResults: true })`:

```ts
import pg from 'pg';
import { PostgresPreferenceStore, PostgresQuietHoursStore, PostgresStore } from 'campanita';

const db = new pg.Pool({ connectionString: process.env.DATABASE_URL });
const store = new PostgresStore(db);
const preferences = new PostgresPreferenceStore(db);
const quietHours = new PostgresQuietHoursStore(db);
```

Con [postgres.js](https://github.com/porsager/postgres), `fromPostgresJs` lo adapta:

```ts
import postgres from 'postgres';
import { fromPostgresJs, PostgresStore } from 'campanita';

const store = new PostgresStore(fromPostgresJs(postgres(process.env.DATABASE_URL!)));
```

Las tablas se crean igual que en D1: `postgresSchema()` devuelve las sentencias para copiar a una
migración, o `await store.createSchema()` las corre. Si escribís la migración a mano, la columna
`id` de los avisos tiene que ser `TEXT COLLATE "C"`: la bandeja ordena por punto de código, y la
colación por omisión de la base (`en_US` y parecidas) ordena distinto. El nombre de cada tabla
se puede cambiar con `{ table }`, hasta 56 caracteres, para que el de sus índices entre en el
límite de Postgres.

Cada operación es una sola sentencia, así que no reserva una conexión para una transacción: sirve
un pool o un driver por HTTP. Dos pedidos simultáneos con la misma `dedupeKey` dejan un solo aviso,
y guardar varias preferencias es todo o nada. Se prueban con las mismas secuencias del spec contra
un Postgres de verdad, por `pg` y por postgres.js.

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

### Canales disponibles por persona

Por omisión, los canales disponibles son los configurados. Si no todas las personas tienen todos,
porque alguien apagó el email o no registró ningún dispositivo, la función `available` lo indica
para cada destinatario y ámbito:

```ts
const notifier = new Notifier({
  policy, catalog, store, preferences, channels,
  available: async (recipientId, scope) => canalesDe(recipientId), // puede ser síncrona
});
```

Un canal que no está disponible no se intenta, aunque la política lo tenga bloqueado en encendido.

### Entregar un aviso ya guardado

Cuando el aviso lo guardó otro, por ejemplo un trigger de la base, o cuando hay que reintentar una
entrega que falló, `deliver` hace sólo la entrega, sin guardar nada:

```ts
const deliveries = await notifier.deliver(aviso); // Map { 'email' => { outcome: 'sent' } }
```

Usa las preferencias, la disponibilidad y el horario de no molestar del momento de la llamada.
No mira la `dedupe_key`: cada llamada intenta los canales otra vez.

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

## Push

`PushChannel` avisa en los dispositivos de cada persona. Separa a quién se le manda de por dónde:
le pide los dispositivos a una función tuya y le pasa el mensaje, por cada uno, al **transporte**
que le corresponde. Hay dos incluidos: `ApnsTransport`, para dispositivos de Apple, y
`WebPushTransport`, para navegadores.

```ts
import { ApnsTransport, PushChannel, renderPush, WebPushTransport } from 'campanita';

const push = new PushChannel({
  transports: {
    apns: new ApnsTransport({ key, keyId, teamId, topic: 'com.example.app', sound: 'default' }),
    web: new WebPushTransport({ publicKey, privateKey, subject: 'mailto:avisos@example.com' }),
  },
  // Del aviso a sus dispositivos. `transport` es la clave de arriba; `target`, lo que ese
  // transporte necesita.
  resolveDevices: async (notification) => {
    const rows = await db.devicesOf(notification.recipientId, notification.scope);
    return rows.map((row) =>
      row.kind === 'apple'
        ? { transport: 'apns', target: { token: row.token, environment: row.environment } }
        : { transport: 'web', target: { endpoint: row.endpoint, p256dh: row.p256dh, auth: row.auth } },
    );
  },
  // Qué pasó con cada uno. `expired`: el dispositivo ya no existe.
  onResult: async (device, result) => {
    if (result.outcome === 'expired') await db.forget(device.target);
  },
});
```

- **Los dispositivos son tuyos.** campanita no los guarda: registrar uno cuando la app o el
  navegador lo informa, y olvidarlo cuando la persona cierra sesión o cuando el servicio dice que
  venció, es de tu aplicación. `resolveDevices` recibe el aviso entero, así que puede elegir según
  el ámbito o la dirección a la que lleva.
- **Un dispositivo roto no frena al resto.** Cada uno se resuelve por separado. El canal contesta
  `sent` si alguno recibió; si ninguno, `failed` cuando alguno falló, `expired` cuando lo que hubo
  fueron vencidos, y `skipped` cuando no había dispositivos o ninguno tenía transporte.
- **Un transporte que falta se saltea.** Si en `transports` no está la clave de un dispositivo, ese
  dispositivo cuenta como `skipped`: se puede configurar un servicio sin el otro.
- **El mensaje por omisión** (`renderPush`) lleva el título, el cuerpo, la dirección del aviso como
  `url` y, en `data`, `notification_id` y `type`, para que quien lo recibe pueda abrir el aviso o
  marcarlo como leído. No lleva hilo: se agrega con `render`, por ejemplo
  `render: (n) => ({ ...renderPush(n), thread: n.category })`.
- **Otro servicio es otro transporte**: un objeto con `send(target, message)` que contesta como un
  canal.

### Dispositivos de Apple

`ApnsTransport` le habla a APNs directo, con la clave .p8 del equipo. El destino es
`{ token, environment }`: el token que el sistema le dio a la app, y `sandbox` o `production` según
cómo se instaló. Ese dato lo sabe la app y lo tiene que mandar al registrarse, porque un token sólo
vale contra su servicio.

- **APNs sólo habla HTTP/2.** El transporte usa el `fetch` global o el que le pases en `fetch`;
  comprobá que el de tu entorno lo negocie antes de confiar en la configuración.
- **En el cuerpo**, los datos del mensaje van en la raíz, junto a `aps`; la dirección va en `url` y
  el hilo en `thread-id`. Sin `sound`, el aviso no suena.
- **`expired`** es lo que APNs contesta para un token que ya no existe o que no es de esta app
  (`Unregistered`, `BadDeviceToken`, `DeviceTokenNotForTopic`, o un 410). Un token mandado al
  entorno equivocado también cae acá.
- **El «provider token» se reusa** 45 minutos por clave, también entre instancias: APNs rechaza
  que se firme uno nuevo en cada pedido.
- **Ningún error repite el token ni la clave.**

### Navegadores

`WebPushTransport` firma con VAPID y cifra el mensaje para el navegador (RFC 8291 y 8292). El
destino es la suscripción, como la entrega `PushSubscription.toJSON()`:
`{ endpoint, p256dh, auth }`.

- **El mensaje llega como JSON** con `title`, `body`, `url`, `tag` (el hilo) y `data`, sin los
  campos vacíos. Mostrarlo es de tu service worker; suscribir al navegador, de tu interfaz.
- **El `endpoint` lo informa el navegador**, es decir quien usa tu app. Sólo se acepta una URL
  `https:` y no se siguen redirecciones; con `acceptEndpoint` se limita a los servicios que
  quieras.
- **`expired`** es un 404 o un 410: la suscripción ya no existe.
- **Un mensaje que no entra** en lo que un servicio está obligado a aceptar (4096 bytes ya cifrado)
  es `failed`, sin salir a la red.
- **`ttlSeconds`** es cuánto guarda el servicio un aviso que no pudo entregar. Por omisión, un día.

Los dos transportes están probados con un `fetch` de prueba, no contra los servicios: conviene
mandar un aviso real antes de confiar en la configuración.

## La campanita en tu app

La bandeja, las preferencias y el horario de no molestar se sirven por HTTP con un contrato fijo
(sección "La bandeja por HTTP" del [spec](../spec/SPEC.md)). Del lado del servidor hay un
adaptador; del lado de la interfaz, un cliente, un store y dos hooks de React. El dibujo de la
campana es tuyo: campanita no trae componentes ni estilos.

### En el servidor: `campanita/http`

`createInboxHandler` recibe un `Request` y devuelve un `Response`, o `null` si la ruta no es de la
bandeja. Sirve en Workers, Hono, Next, Deno, Bun y Node 18+, sin dependencias.

```ts
import { D1PreferenceStore, D1QuietHoursStore, D1Store } from 'campanita';
import { createInboxHandler } from 'campanita/http';

const inbox = createInboxHandler({
  store: new D1Store(env.DB),
  basePath: '/api/avisos',
  resolveRecipient: (request) => sesion(request)?.userId ?? null,   // null: 401
  // Opcionales: con política y preferencias se sirve /preferences; con horarios, /quiet-hours.
  policy,
  preferences: new D1PreferenceStore(env.DB),
  quietHours: new D1QuietHoursStore(env.DB),
  // La misma función que recibe el Notifier: (recipientId, scope). Por omisión, todos los de la política.
  available: (userId, scope) => canalesDe(userId),
});

// En Hono, por ejemplo:
app.all('/api/avisos/*', async (c) => (await inbox(c.req.raw)) ?? c.notFound());
```

| Ruta | Qué hace |
|---|---|
| `GET /feed?limit&cursor&unread_only&category&scope` | una página de la bandeja, del más nuevo al más viejo |
| `GET /unread-count?scope&cap` | el contador, con `capped` para mostrar `99+` |
| `POST /feed/{id}/read` | marca uno |
| `POST /feed/read-all` | marca hasta lo que la persona vio (`before`) |
| `GET` / `PATCH /preferences?scope` | la matriz de preferencias y su cambio |
| `GET` / `PUT /quiet-hours` | el horario de no molestar |

- **El destinatario sale de `resolveRecipient`, nunca del pedido.** Una persona no puede pedir la
  bandeja de otra.
- **`scope` es un filtro, no un permiso**: trae lo de ese ámbito más lo de cuenta. Si tu producto
  necesita que el ámbito sea uno de los de la persona, revisalo antes de llamar al adaptador.
- El contrato no autentica ni limita el ritmo de pedidos: eso va en tu router.

### En la interfaz: `campanita/client` y `campanita/react`

```ts
import { createInboxClient, createInboxStore } from 'campanita/client';

const client = createInboxClient({
  baseUrl: 'https://api.example.com/api/avisos',
  fetch: (url, init) => fetch(url, { ...init, credentials: 'include' }),
});
const inbox = createInboxStore(client, { scope: proyectoActivo, pollMs: 30_000 });
```

El store guarda la primera página y el contador. Mientras alguien lo escucha, pide el contador
cada `pollMs` (una consulta barata) y vuelve a pedir la página sólo cuando el número cambia.
`markRead` y `markAllRead` son optimistas y vuelven atrás si el servidor falla; `markAllRead`
marca lo que la persona tiene a la vista, no lo que llegó después.

```tsx
import { useInbox } from 'campanita/react';

function Campana({ inbox }: { inbox: InboxStore }) {
  const { items, unread, hasMore } = useInbox(inbox);
  return (
    <Menu badge={unread.capped ? `${unread.count}+` : unread.count}>
      {items.map((n) => (
        <Item key={n.id} unread={n.read_at === null} onClick={() => inbox.markRead(n.id)}>
          {n.title}
        </Item>
      ))}
      {hasMore && <button onClick={() => inbox.loadMore()}>Ver más</button>}
      <button onClick={() => inbox.markAllRead()}>Marcar todo como leído</button>
    </Menu>
  );
}
```

React es una dependencia opcional: sólo la necesita `campanita/react`. El cliente y el store no
dependen de nada.

`runHttpVector` corre los vectores de `spec/vectors/http/` contra un adaptador, para quien monte el
suyo sobre otro almacén.

## Desarrollo

Desde la raíz del repo:

```bash
pnpm install
pnpm test
pnpm typecheck
pnpm build
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de Python.
