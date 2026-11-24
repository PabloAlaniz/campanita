# Puertos

El núcleo de campanita decide; no guarda, no envía y no dibuja. Todo eso entra por cuatro puertos.
Están declarados en [`python/src/campanita/ports.py`](../python/src/campanita/ports.py) y en
[`ts/src/ports.ts`](../ts/src/ports.ts).

| Puerto | Qué resuelve | Estado |
|---|---|---|
| `NotificationStore` | Guardar avisos y leer la bandeja | Especificado, con secuencias de conformidad |
| `PreferenceStore` | Guardar lo que cambió cada persona | Especificado, con secuencias de conformidad |
| `QuietHoursStore` | Guardar el horario de no molestar de cada persona | Especificado, con secuencias de conformidad |
| `DeliveryChannel` | Entregar un aviso fuera de la bandeja | Especificado dentro del recorrido; incluye email, Slack y push |

La **bandeja** no es un puerto: es lo que se lee del `NotificationStore`. Cualquier superficie
que la muestre (un panel web, una app, un bot) es un cliente de esa lectura, y para eso está el
contrato de la sección "La bandeja por HTTP" del [spec](../spec/SPEC.md): un adaptador que lo sirve
sobre cualquier almacén (`createInboxHandler` en TypeScript, `InboxApp` en Python) y un cliente
para la interfaz (`campanita/client`). Un adaptador HTTP propio es correcto cuando reproduce las
secuencias de [`spec/vectors/http/`](../spec/vectors/http).

## El almacén de avisos

Su comportamiento está en la sección "La bandeja" del [spec](../spec/SPEC.md) y en las
secuencias de [`spec/vectors/bandeja/`](../spec/vectors/bandeja). Un adaptador es correcto
cuando las reproduce todas.

Adaptadores incluidos:

| Adaptador | Python | TypeScript |
|---|---|---|
| En memoria | `campanita.stores.MemoryStore` | `MemoryStore` |
| SQLite | `campanita.stores.SqliteStore` | todavía no |
| Cloudflare D1 | no aplica | `D1Store` |
| Postgres | `campanita.stores.PostgresStore` | `PostgresStore` |

## El almacén de preferencias

Su comportamiento está en la sección "Las preferencias guardadas" del spec y en las secuencias de
[`spec/vectors/preferencias-guardadas/`](../spec/vectors/preferencias-guardadas).

| Adaptador | Python | TypeScript |
|---|---|---|
| En memoria | `campanita.stores.MemoryPreferenceStore` | `MemoryPreferenceStore` |
| SQLite | `campanita.stores.SqlitePreferenceStore` | todavía no |
| Cloudflare D1 | no aplica | `D1PreferenceStore` |
| Postgres | `campanita.stores.PostgresPreferenceStore` | `PostgresPreferenceStore` |

## El almacén de horarios

Guarda el horario de no molestar de cada persona. Su comportamiento está en la sección "No
molestar" del spec y en las secuencias de
[`spec/vectors/silencio-guardado/`](../spec/vectors/silencio-guardado). Es opcional: sin él,
nadie está en silencio.

| Adaptador | Python | TypeScript |
|---|---|---|
| En memoria | `campanita.stores.MemoryQuietHoursStore` | `MemoryQuietHoursStore` |
| SQLite | `campanita.stores.SqliteQuietHoursStore` | todavía no |
| Cloudflare D1 | no aplica | `D1QuietHoursStore` |
| Postgres | `campanita.stores.PostgresQuietHoursStore` | `PostgresQuietHoursStore` |

Las tablas pueden vivir en la misma base o en bases distintas: los puertos son independientes.

## El canal de entrega

Un canal tiene un identificador (el mismo que usa la política) y una operación: recibe un aviso
guardado y contesta `sent`, `expired`, `failed` o `skipped`. Lo usa el recorrido completo
(`Notifier`), que está en la sección "El recorrido completo" del spec y en
[`spec/vectors/recorrido/`](../spec/vectors/recorrido).

Un canal mínimo:

```python
from campanita import Notification
from campanita.ports import DeliveryResult

class Consola:
    id = "console"

    async def send(self, notification: Notification) -> DeliveryResult:
        print(f"[{notification.recipient_id}] {notification.title}")
        return DeliveryResult("sent")
```

```ts
import type { DeliveryChannel, DeliveryResult, Notification } from 'campanita';

const consola: DeliveryChannel = {
  id: 'console',
  async send(notification: Notification): Promise<DeliveryResult> {
    console.log(`[${notification.recipientId}] ${notification.title}`);
    return { outcome: 'sent' };
  },
};
```

### Email

El canal de email (`EmailChannel`) separa qué se dice de con qué se manda. Busca la dirección de
la persona con una función de quien integra, arma el mensaje y se lo pasa a un **transporte**, que
es un puerto más chico: `send(mensaje)`.

| Transporte | Python | TypeScript |
|---|---|---|
| SMTP | `campanita.channels.SmtpTransport` | todavía no |
| HTTP genérico | `campanita.channels.HttpEmailTransport` | `HttpEmailTransport` |
| Resend | `campanita.channels.ResendTransport` | `ResendTransport` |

- El **HTTP genérico** sirve para cualquier servicio de email con una API: se le pasa la URL, las
  cabeceras y, si el servicio espera otra forma, cómo armar el cuerpo. Es la forma de conectar un
  servicio propio sin escribir un transporte. Si el servicio contesta 2xx también cuando no mandó
  el mensaje, `readResult` (`read_result=`) lee el resultado del cuerpo: `sent`, `failed` o
  `skipped`.
- El mensaje por omisión y la validez de una dirección están en la sección "Email" del spec y en
  [`spec/vectors/email/`](../spec/vectors/email). El envío en sí es de cada transporte.

Lo que cada transporte garantiza por su cuenta:

- **No sigue redirecciones**, para no reenviar las credenciales a otro servidor.
- **El error no repite credenciales ni el cuerpo de la respuesta**: sólo el código o el tipo de
  error.
- **SMTP verifica el certificado** del servidor y no manda la contraseña por una conexión sin
  cifrar.

Los transportes están probados contra servidores de prueba locales. Ninguno se probó contra un
proveedor real.

### Slack

El canal de Slack (`SlackChannel`) publica en el canal que conectó cada persona, por un «incoming
webhook». Busca la URL del webhook con una función de quien integra, igual que el de email busca
la dirección.

La conexión se hace con OAuth y dos funciones: una arma la URL a la que se manda a la persona y
la otra cambia el código de vuelta por la conexión. La persona elige el canal en la pantalla de
Slack, que sólo le ofrece los que puede usar. Guardar la conexión y comprobar el `state` es de
quien integra.

- **Sólo publica en `https://hooks.slack.com/services/…`**, sin seguir redirecciones.
- **Ningún error repite la URL del webhook, el secreto ni el código.**
- **Un webhook quitado o un canal archivado es `expired`**, no `failed`: reintentar no sirve.

Slack es un adaptador y no está en el spec. Lo que tiene que dar igual en los dos lenguajes (el
mensaje por omisión, qué URL se acepta y cómo se lee la respuesta de la conexión) está en
[`fixtures/slack.json`](../fixtures/slack.json), que leen los tests de los dos paquetes.

### Push

El canal de push (`PushChannel`) separa a quién se le manda de por dónde. Busca los dispositivos
del aviso con una función de quien integra y le pasa el mensaje, por cada uno, a un
**transporte**, que es un puerto más chico: `send(destino, mensaje)`.

| Transporte | Python | TypeScript |
|---|---|---|
| APNs (dispositivos de Apple) | todavía no | `ApnsTransport` |
| Web Push (navegadores) | todavía no | `WebPushTransport` |

Los transportes firman con ES256 y, el de Web Push, cifran: en TypeScript eso es Web Crypto. En
Python no está en la biblioteca estándar, y APNs pide además HTTP/2; por eso ahí van a ser un
extra opcional y no parte del paquete base.

- **Cada dispositivo se resuelve por separado**, y el canal resume: `sent` si alguno recibió; si
  ninguno, `failed` cuando alguno falló, `expired` cuando lo que hubo fueron vencidos, y `skipped`
  cuando no había a dónde mandar.
- **El resultado de cada dispositivo llega por `onResult`** (`on_result`). Con `expired`, quien
  integra olvida ese dispositivo.
- **Un transporte que no está configurado se saltea**, sin impedir los demás.
- **Ningún error repite un token, un endpoint ni una clave.** No se siguen redirecciones.
- **Web Push sólo manda a una URL `https:`**: el endpoint lo informa el navegador de quien usa la
  aplicación, y `acceptEndpoint` permite limitarlo a los servicios conocidos.

Push es un adaptador y no está en el spec. Lo que tiene que dar igual en los dos lenguajes (el
mensaje por omisión y cómo se resume un envío a varios dispositivos) está en
[`fixtures/push.json`](../fixtures/push.json), que leen los tests de los dos paquetes.

Los transportes están probados con un `fetch` de prueba, no contra los servicios.

## Escribir un adaptador

Cada paquete trae el corredor de secuencias que usa para sus propios adaptadores:

```python
import json
from pathlib import Path
from campanita.conformance import run_store_vector

for path in sorted(Path("spec/vectors/bandeja").glob("*.json")):
    await run_store_vector(json.loads(path.read_text()), lambda: MiAlmacen(...))
```

```ts
import { readdirSync, readFileSync } from 'node:fs';
import { runStoreVector } from 'campanita';

for (const file of readdirSync('spec/vectors/bandeja')) {
  const vector = JSON.parse(readFileSync(`spec/vectors/bandeja/${file}`, 'utf8'));
  await runStoreVector(vector, () => new MiAlmacen(/* … */));
}
```

Para un almacén de preferencias es igual, con `run_preference_vector` / `runPreferenceVector` y
las secuencias de `spec/vectors/preferencias-guardadas/`; y para uno de horarios, con
`run_quiet_hours_vector` / `runQuietHoursVector` y las de `spec/vectors/silencio-guardado/`.

Cada secuencia necesita un almacén vacío. Si un paso no coincide, el corredor dice cuál, qué
esperaba y qué obtuvo.

Lo que las secuencias **no** pueden probar, y cada adaptador tiene que garantizar por su cuenta:

- **Que revisar la clave y guardar sea atómico.** Dos pedidos simultáneos con la misma
  `dedupe_key` dejan un solo aviso. En SQL, eso es un índice único sobre `(recipient_id,
  dedupe_key)`.
- **Que el texto se compare byte a byte.** El desempate por `id` y la comparación de
  `recipient_id` no pueden depender de mayúsculas, acentos ni de la configuración regional de la
  base.
- **Que las fechas no pierdan los milisegundos.**
- **Que guardar varios cambios de preferencias sea todo o nada.**

## Decisiones que valen para todos los puertos

- **Toda lectura pide el destinatario.** Es el único alcance de seguridad, y por eso es un
  parámetro obligatorio y no algo que quien llama se tiene que acordar de filtrar.
- **La fecha de creación la pone quien guarda.** Si la pusiera quien emite, un reloj corrido
  desordenaría la bandeja.
- **Las preferencias guardan sólo los cambios.** Sin filas, valen los valores de la política.
  Agregar una categoría o un canal no obliga a migrar datos.
- **Un canal de entrega nunca lanza.** Devuelve `sent`, `expired` o `failed`. Un destino roto no
  puede impedir avisarle al resto, y `expired` le dice al adaptador que olvide ese destino.
- **Cada canal resuelve sus destinos.** El núcleo le pasa el aviso; a qué dirección o a qué
  dispositivos va es asunto del adaptador.

## La regla de los dos adaptadores

Un puerto no se da por estable hasta tener **dos adaptadores reales**. Con uno solo, el puerto
termina teniendo la forma de ese adaptador y el segundo no entra.

Los almacenes la cumplen en los dos lenguajes (en memoria, SQLite y Postgres en Python; en memoria,
D1 y Postgres en TypeScript), y el transporte de email también. El canal de entrega también: email,
Slack y push. El transporte de push, en TypeScript: APNs y Web Push.
