# campanita (Python)

El núcleo de [campanita](https://github.com/PabloAlaniz/campanita) en Python: decide por qué
canales sale un aviso, qué puede cambiar el usuario y cómo se arma un aviso. Sin dependencias.

```bash
pip install campanita
```

> **Versión temprana.** Implementa la versión 0 del spec. Hasta la 1.0, la API puede cambiar
> entre versiones menores; el [CHANGELOG](https://github.com/PabloAlaniz/campanita/blob/main/CHANGELOG.md)
> dice qué cambió.

```python
from campanita import parse_policy, resolve_channels, effective_matrix, validate_patch

policy = parse_policy(
    {
        "security": {
            "inapp": {"default": True, "locked": True},
            "email": {"default": True, "locked": True},
        },
        "activity": {
            "inapp": {"default": True, "locked": True},
            "email": {"default": False},
            "push": {"default": False},
        },
    }
)

disponibles = {"email"}  # lo que esta instalación puede entregar
overrides = {"activity": {"email": True}}  # lo que cambió la persona

resolve_channels(policy, "activity", overrides, disponibles)  # frozenset({'email'})
resolve_channels(policy, "security", {"security": {"email": False}}, disponibles)
# frozenset({'email'}): seguridad está bloqueada, el override no la apaga

effective_matrix(policy, overrides, disponibles)["activity"]["push"]
# MatrixCell(enabled=False, locked=True): push todavía no está disponible

validate_patch(policy, {"activity": {"email": False}}, disponibles)
# (Change(category='activity', channel='email', enabled=False),)
validate_patch(policy, {"security": {"email": False}}, disponibles)
# PatchRejected: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

Para armar un aviso a partir de un evento:

```python
from campanita import parse_catalog, build_notification, EventRejected

catalog = parse_catalog(
    {
        "comments.new": {
            "category": "activity",
            "title": "{author} comentó en {document}",
            "body": "{excerpt}",
        },
    },
    policy,
)

draft = build_notification(
    catalog,
    {
        "type": "comments.new",
        "recipient_id": "user-7",
        "payload": {"author": "Ana", "document": "Presupuesto", "excerpt": "¿Va con IVA?"},
        "dedupe_key": "comment-381",
    },
)
draft.title  # 'Ana comentó en Presupuesto'
draft.category  # 'activity'

build_notification(catalog, {"type": "comments.new", "recipient_id": "user-7", "payload": {}})
# EventRejected: evento rechazado: falta una variable (author)
```

Las plantillas sólo reemplazan `{variables}` por texto o enteros. No hay formatos ni expresiones:
lo que hacen es igual en todos los lenguajes.

## La bandeja

Un almacén guarda los avisos y contesta la bandeja. Hay tres incluidos (en memoria, SQLite y
Postgres), y todos pasan las mismas secuencias del spec:

```python
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4
from campanita import FeedQuery
from campanita.stores import MemoryStore, SqliteStore

store = SqliteStore(sqlite3.connect("avisos.db", isolation_level=None))
store.create_schema()  # o MemoryStore(), sin base

added = await store.add(draft, id=str(uuid4()), created_at=datetime.now(UTC))
added.created  # False si esa persona ya tenía un aviso con la misma dedupe_key

page = await store.list_feed(FeedQuery("user-7", limit=20))
if page.next_cursor:
    older = await store.list_feed(FeedQuery("user-7", limit=20, cursor=page.next_cursor))

await store.count_unread("user-7", cap=100)
await store.mark_read(page.items[0].id, "user-7", datetime.now(UTC))
await store.mark_all_read("user-7", before=page.items[0].created_at, read_at=datetime.now(UTC))
```

`SqliteStore` usa `sqlite3` de la biblioteca estándar y bloquea mientras corre cada consulta:
alcanza para aplicaciones chicas, herramientas y tests.

### Postgres

`PostgresStore`, `PostgresPreferenceStore` y `PostgresQuietHoursStore` guardan en Postgres con
[psycopg 3](https://www.psycopg.org/psycopg3/) en modo asíncrono. campanita no depende de psycopg:
se instala aparte (`pip install "psycopg[binary]" psycopg-pool`). Reciben una `AsyncConnection` o
un `AsyncConnectionPool` y no los cierran:

```python
from psycopg_pool import AsyncConnectionPool
from campanita.stores import PostgresPreferenceStore, PostgresQuietHoursStore, PostgresStore

pool = AsyncConnectionPool("postgresql://…", open=False)
await pool.open()
store = PostgresStore(pool)
preferences = PostgresPreferenceStore(pool)
quiet_hours = PostgresQuietHoursStore(pool)
await store.create_schema()  # o las sentencias de postgres_schema() en una migración
```

Con un pool, cada operación toma una conexión y la devuelve. Con una conexión, cada operación corre
en su propia transacción, con autocommit o sin él; si la conexión ya está dentro de una transacción
tuya, corre en un savepoint y se confirma o se deshace con ella. Una conexión hace una cosa por vez:
las operaciones de los almacenes sobre la misma conexión se turnan, así que para atender pedidos a
la vez conviene un pool. El `row_factory` y el `cursor_factory` de la conexión no importan.

Si escribís la migración a mano, la columna `id` de los avisos tiene que ser `TEXT COLLATE "C"`: la
bandeja ordena por punto de código, y la colación por omisión de la base ordena distinto. Postgres
no guarda el carácter nulo (`\x00`) en una columna de texto: un aviso con uno en el título, el
cuerpo o algún identificador falla con `psycopg.errors.DataError`. En el payload sí puede ir.

Para otra base, escribí un adaptador que cumpla `campanita.ports.NotificationStore` y probalo con
`campanita.conformance.run_store_vector`: ver [`docs/puertos.md`](../docs/puertos.md).

## Las preferencias guardadas

Lo que cambió cada persona va a un almacén de preferencias. El recorrido completo de una pantalla
de preferencias es validar, guardar y volver a leer:

```python
from campanita import effective_matrix, validate_patch
from campanita.stores import MemoryPreferenceStore  # o SqlitePreferenceStore(conexión)

preferences = MemoryPreferenceStore()

changes = validate_patch(policy, {"activity": {"email": True}}, disponibles)  # rechaza lo bloqueado
await preferences.save_changes("user-7", changes)

overrides = await preferences.get_overrides("user-7")
effective_matrix(policy, overrides, disponibles)  # lo que muestra la pantalla
resolve_channels(policy, "activity", overrides, disponibles)  # por dónde sale el próximo aviso
```

## El recorrido completo

`Notifier` junta todo: arma el aviso, lo guarda, lee las preferencias de esa persona y lo entrega
por los canales que corresponden.

```python
from campanita import Notification, Notifier
from campanita.ports import DeliveryResult


class Consola:
    id = "email"  # el identificador que usa la política

    async def send(self, notification: Notification) -> DeliveryResult:
        print(f"[{notification.recipient_id}] {notification.title}")
        return DeliveryResult("sent")  # o "expired", o "failed"


notifier = Notifier(
    policy=policy, catalog=catalog, store=store, preferences=preferences, channels=[Consola()]
)

result = await notifier.notify({"type": "comments.new", "recipient_id": "user-7", "payload": {...}})
result.created  # False si la dedupe_key ya existía: no se vuelve a entregar
result.deliveries  # {'email': DeliveryResult(outcome='sent', ...)}
```

Primero se guarda y después se entrega: un canal que falla, o que lanza un error, no deshace el
aviso ni impide intentar los demás. Un canal es cualquier objeto con `id` y `send`.

### Canales disponibles por persona

Por omisión, los canales disponibles son los configurados. Si no todas las personas tienen todos,
porque alguien apagó el email o no registró ningún dispositivo, la función `available` lo indica
para cada destinatario y ámbito:

```python
notifier = Notifier(
    policy=policy,
    catalog=catalog,
    store=store,
    preferences=preferences,
    channels=channels,
    available=canales_de,  # (recipient_id, scope) -> canales; puede ser async
)
```

Un canal que no está disponible no se intenta, aunque la política lo tenga bloqueado en encendido.

### Entregar un aviso ya guardado

Cuando el aviso lo guardó otro, por ejemplo un trigger de la base, o cuando hay que reintentar una
entrega que falló, `deliver` hace sólo la entrega, sin guardar nada:

```python
deliveries = await notifier.deliver(aviso)  # {'email': DeliveryResult(outcome='sent', ...)}
```

Usa las preferencias, la disponibilidad y el horario de no molestar del momento de la llamada.
No mira la `dedupe_key`: cada llamada intenta los canales otra vez.

### Avisos para un grupo

Un tipo con `"audience": "group"` le habla a un grupo (un equipo, un proyecto) y no a una persona.
El `recipient_id` es el del grupo: el aviso sale **una sola vez**, por los canales que eligió el
grupo y a sus destinos (un canal de Slack compartido, por ejemplo). A cada miembro le queda una
copia en su bandeja, que no se entrega por ningún canal.

```python
catalog = parse_catalog(
    {
        "report.ready": {"category": "reports", "title": "Informe listo: {name}", "audience": "group"},
        "report.mine": {"category": "reports", "title": "Tu informe está listo: {name}"},  # "person"
    },
    policy,
)

await notifier.notify(
    {"type": "report.ready", "recipient_id": "team-3", "scope": "project-9", "payload": {"name": "Marzo"}},
    members=[{"recipient_id": "user-7", "id": "n-1"}, {"recipient_id": "user-8", "id": "n-2"}],
)
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

```python
from campanita import parse_quiet_hours
from campanita.stores import MemoryQuietHoursStore  # o SqliteQuietHoursStore(conexión)

quiet_hours = MemoryQuietHoursStore()
await quiet_hours.set_quiet_hours(
    "user-7",
    parse_quiet_hours({"enabled": True, "start": "22:00", "end": "08:00", "timezone": "America/Lima"}),
)

notifier = Notifier(
    policy=policy,
    catalog=catalog,
    store=store,
    preferences=preferences,
    channels=channels,
    quiet_hours=quiet_hours,
)
result = await notifier.notify(evento)  # a las 23:30 de esa persona
result.deliveries  # {'email': DeliveryResult(outcome='silenced', ...)}
```

Una celda de la política con `urgent: true` se entrega igual:
`{"default": True, "locked": True, "urgent": True}`. La hora local sale de `zoneinfo`, que
en Windows necesita el paquete `tzdata`; si la plataforma no conoce la zona, se entrega. Lo silenciado no se manda después: eso lo decide quien
integra.

## La bandeja por HTTP

La bandeja, las preferencias y el horario de no molestar se sirven por HTTP con un contrato fijo
(sección "La bandeja por HTTP" del [spec](../spec/SPEC.md)): la misma interfaz habla igual con un
backend en Python o en TypeScript. `InboxApp` es una aplicación ASGI, sin dependencias, que se monta
bajo el prefijo que elijas:

```python
from fastapi import FastAPI, Request  # o Starlette: es lo mismo
from campanita.http import InboxApp
from campanita.stores import SqlitePreferenceStore, SqliteQuietHoursStore, SqliteStore


def quien_pide(scope) -> str | None:
    usuario = sesion(Request(scope))  # tu sesión o tu token
    return usuario.id if usuario else None  # None: 401


app = FastAPI()
app.mount(
    "/api/avisos",
    InboxApp(
        store=SqliteStore(conexion),
        resolve_recipient=quien_pide,  # también puede ser async
        # Opcionales: con política y preferencias se sirve /preferences; con horarios, /quiet-hours.
        policy=policy,
        preferences=SqlitePreferenceStore(conexion),
        quiet_hours=SqliteQuietHoursStore(conexion),
        # La misma función que recibe el Notifier: (recipient_id, scope). Por omisión, todos los de la política.
        available=canales_de,
    ),
)
```

| Ruta | Qué hace |
|---|---|
| `GET /feed?limit&cursor&unread_only&category&scope` | una página de la bandeja, del más nuevo al más viejo |
| `GET /unread-count?scope&cap` | el contador, con `capped` para mostrar `99+` |
| `POST /feed/{id}/read` | marca uno |
| `POST /feed/read-all` | marca hasta lo que la persona vio (`before`) |
| `GET` / `PATCH /preferences?scope` | la matriz de preferencias y su cambio |
| `GET` / `PUT /quiet-hours` | el horario de no molestar |

- **El destinatario sale de `resolve_recipient`, nunca del pedido.** Una persona no puede pedir
  la bandeja de otra.
- **`scope` es un filtro, no un permiso**: trae lo de ese ámbito más lo de cuenta. Si tu producto
  necesita que el ámbito sea uno de los de la persona, revisalo en `resolve_recipient` o antes.
- **Es una aplicación entera**: lo que no es del contrato contesta `404`. Respeta `root_path`,
  así que funciona montada en cualquier prefijo.
- El contrato no autentica ni limita el ritmo de pedidos: eso va en tu aplicación.

`campanita.http` también trae los ayudantes del cable (`encode_cursor`, `decode_cursor`,
`to_iso`, `from_iso`, `item_to_wire`), para quien escriba el adaptador de otro framework.

## Email

`EmailChannel` busca la dirección de la persona, arma el mensaje y se lo pasa a un transporte. El
transporte se cambia sin tocar el resto:

```python
from campanita.ports import DeliveryResult
from campanita.channels import EmailChannel, HttpEmailTransport, ResendTransport, SmtpTransport


async def buscar_direccion(recipient_id: str) -> str | None: ...  # de tu base de usuarios; None si no tiene


email = EmailChannel(
    transport=SmtpTransport("smtp.example.com", username="avisos", password="…"),
    sender="avisos@example.com",
    sender_name="Avisos",
    resolve_address=buscar_direccion,
)

# El mismo canal, con otro transporte:
ResendTransport("re_…")
HttpEmailTransport(
    "https://mail.example.com/v1/send",  # cualquier servicio de email con API HTTP
    headers={"X-Api-Key": "…"},
    build_body=lambda m: {"to": m.to, "subject": m.subject, "body": m.text},
)


# Un servicio que contesta 200 también cuando no mandó el mensaje, y lo dice en el cuerpo:
def leer_resultado(r: dict) -> DeliveryResult:
    if r["ok"]:
        return DeliveryResult("sent", provider_message_id=r.get("id"))
    if r.get("code") == "unsubscribed":
        return DeliveryResult("skipped", error=r["code"])
    return DeliveryResult("failed", error=r.get("code"))


HttpEmailTransport(
    "https://mail.example.com/v1/send",
    headers={"X-Api-Key": "…"},
    # El identificador del aviso, para que un reintento no mande el mensaje dos veces.
    build_body=lambda m: {
        "to": m.to,
        "subject": m.subject,
        "body": m.text,
        "idempotency_key": m.idempotency_key,
    },
    read_result=leer_resultado,
)
```

- **Sin dirección no es una falla.** Si la persona no tiene dirección, o no es válida, la entrega
  queda como `skipped` y el aviso sigue en la bandeja.
- **El mensaje por omisión** lleva el título como asunto, y el cuerpo y el enlace como texto. Se
  cambia con `render=`; el asunto nunca sale con saltos de línea.
- **`SmtpTransport`** usa `starttls` por omisión (`security="tls"` para TLS directo), verifica el
  certificado y corre en un hilo aparte. No manda la contraseña por una conexión sin cifrar.
- **Los transportes HTTP** no siguen redirecciones y no repiten credenciales en los errores.
- **Un 2xx es `sent`**, salvo que se pase `read_result=`: entonces el resultado lo dice el cuerpo
  (`sent`, `failed` o `skipped`). Un cuerpo que no se entiende es `failed`.

Los transportes están probados contra servidores de prueba locales, incluido SMTP con TLS.
Ninguno se probó contra un proveedor real: conviene hacer un envío de prueba antes de confiar en
la configuración.

## Slack

`SlackChannel` avisa en el canal de Slack que conectó cada persona. La conexión es por OAuth: la
persona elige el canal en la pantalla de Slack, entre los que puede usar, y nunca ve ni pega un
token. Lo que queda es la URL de un webhook, que guarda quien integra.

```python
from campanita.channels import SlackChannel, exchange_slack_code, slack_authorize_url

# 1. Mandar a la persona a Slack. `state` es tuyo: atado a su sesión e imposible de adivinar.
url = slack_authorize_url(client_id=client_id, redirect_uri=redirect_uri, state=state)

# 2. Slack la devuelve a `redirect_uri` con `code` y `state`. Comprobá `state` y cambiá el código:
connection = await exchange_slack_code(
    client_id=client_id, client_secret=client_secret, code=code, redirect_uri=redirect_uri
)
guardar_conexion(recipient_id, connection)  # webhook_url es un secreto; channel es «#avisos»

# 3. El canal pide la URL cuando hay algo que entregar.
slack = SlackChannel(resolve_webhook=buscar_webhook)  # tu función: None si no conectó

# O, con un canal distinto por ámbito (un proyecto, un equipo), a partir del aviso entero:
por_ambito = SlackChannel(resolve_webhook_for=lambda n: buscar_webhook(n.recipient_id, n.scope))
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
  persona lo quita desde ahí (`configuration_url`).

Está probado con un `POST` de prueba, no contra Slack: conviene hacer una conexión real antes de
confiar en la configuración.

## Push

`PushChannel` avisa en los dispositivos de cada persona. Separa a quién se le manda de por dónde:
le pide los dispositivos a una función tuya y le pasa el mensaje, por cada uno, al **transporte**
que le corresponde.

Este paquete trae el canal. Los transportes incluidos (dispositivos de Apple y navegadores) están,
por ahora, sólo en el de TypeScript: acá se escribe uno propio, que es un objeto con
`send(target, message)`.

```python
from campanita.channels import PushChannel, PushDevice, PushMessage
from campanita.ports import DeliveryResult


class MiServicio:
    async def send(self, target: str, message: PushMessage) -> DeliveryResult:
        ...  # mandar `message` a `target`
        return DeliveryResult("sent")


async def buscar_dispositivos(notification):
    rows = await db.devices_of(notification.recipient_id, notification.scope)
    return [PushDevice(transport="servicio", target=row.token) for row in rows]


async def anotar(device, result, notification):
    if result.outcome == "expired":  # el dispositivo ya no existe
        await db.forget(device.target)


push = PushChannel(
    transports={"servicio": MiServicio()},
    resolve_devices=buscar_dispositivos,
    on_result=anotar,
)
```

- **Los dispositivos son tuyos.** campanita no los guarda: registrar uno y olvidarlo es de tu
  aplicación. `resolve_devices` recibe el aviso entero, así que puede elegir según el ámbito o la
  dirección a la que lleva.
- **Un dispositivo roto no frena al resto.** Cada uno se resuelve por separado. El canal contesta
  `sent` si alguno recibió; si ninguno, `failed` cuando alguno falló, `expired` cuando lo que hubo
  fueron vencidos, y `skipped` cuando no había dispositivos o ninguno tenía transporte.
- **Un transporte que falta se saltea.** Si en `transports` no está la clave de un dispositivo, ese
  dispositivo cuenta como `skipped`.
- **El mensaje por omisión** (`render_push`) lleva el título, el cuerpo, la dirección del aviso
  como `url` y, en `data`, `notification_id` y `type`. No lleva hilo: se agrega con `render`.

## Desarrollo

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check .
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de
TypeScript.
