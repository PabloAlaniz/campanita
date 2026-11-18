# Puertos

El núcleo de campanita decide; no guarda, no envía y no dibuja. Todo eso entra por cuatro puertos.
Están declarados en [`python/src/campanita/ports.py`](../python/src/campanita/ports.py) y en
[`ts/src/ports.ts`](../ts/src/ports.ts).

| Puerto | Qué resuelve | Estado |
|---|---|---|
| `NotificationStore` | Guardar avisos y leer la bandeja | Especificado, con secuencias de conformidad |
| `PreferenceStore` | Guardar lo que cambió cada persona | Especificado, con secuencias de conformidad |
| `QuietHoursStore` | Guardar el horario de no molestar de cada persona | Especificado, con secuencias de conformidad |
| `DeliveryChannel` | Entregar un aviso fuera de la bandeja | Especificado dentro del recorrido; el primer canal es email |

La **bandeja** no es un puerto: es lo que se lee del `NotificationStore`. Cualquier superficie
que la muestre (un panel web, una app, un bot) es un cliente de esa lectura.

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

## El almacén de preferencias

Su comportamiento está en la sección "Las preferencias guardadas" del spec y en las secuencias de
[`spec/vectors/preferencias-guardadas/`](../spec/vectors/preferencias-guardadas).

| Adaptador | Python | TypeScript |
|---|---|---|
| En memoria | `campanita.stores.MemoryPreferenceStore` | `MemoryPreferenceStore` |
| SQLite | `campanita.stores.SqlitePreferenceStore` | todavía no |
| Cloudflare D1 | no aplica | `D1PreferenceStore` |

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
  servicio propio sin escribir un transporte.
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

Los dos almacenes la cumplen en los dos lenguajes (en memoria y SQLite en Python; en memoria y D1
en TypeScript), y el transporte de email también. El canal de entrega tiene hoy un solo canal real, el de email: le falta el segundo.
