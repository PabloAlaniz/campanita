# Especificación de campanita, versión 0 (borrador)

Todo port de campanita tiene que comportarse como se describe acá. Los archivos JSON de
`vectors/` son la prueba normativa: un port cumple cuando pasa todos los vectores. Los genera
`tools/gen_vectors.py`, una implementación independiente, para que ningún port se valide a sí
mismo.

La versión 0 es un borrador y puede cambiar sin aviso. Cubre seis cosas:

- **Por qué canales sale un aviso y qué puede cambiar el usuario**: la política, la resolución de
  canales, la matriz efectiva y el cambio de preferencias.
- **Cómo se arma un aviso**: las plantillas, el catálogo de tipos y el paso de un evento a un
  aviso listo para guardar.
- **La bandeja**: guardar un aviso sin duplicarlo, listarlo por páginas, contar los no leídos y
  marcarlos como leídos, sobre cualquier base.
- **Las preferencias guardadas**: dónde queda lo que cambió cada persona, sobre cualquier base.
- **El recorrido completo**: de un evento a un aviso guardado y entregado por los canales que
  corresponden.
- **El email**: qué mensaje sale de un aviso y qué direcciones se aceptan.

Los reintentos de una entrega y su registro todavía no están especificados (ver
`docs/hoja-de-ruta.md`).

El spec no nombra categorías, canales ni tipos propios. Los que aparecen en los ejemplos y en los
vectores son datos de prueba.

## Nombres

El spec nombra las operaciones en `snake_case`. Cada port usa la convención de su lenguaje
(`resolve_channels` en Python, `resolveChannels` en TypeScript). Lo que no cambia entre ports son
los literales que viajan: `inapp`, las claves `default`, `locked` y `enabled`, y los motivos de
rechazo.

## Vocabulario

- Un **canal** es un medio por el que un aviso llega a una persona: `email`, `push`, `sms`.
- Una **categoría** agrupa avisos que comparten reglas: `security`, `activity`.
- Los dos son **identificadores**: texto que cumple `^[a-z][a-z0-9_-]{0,63}$`.
- **`inapp` está reservado.** Es la bandeja, no un canal de entrega. La notificación guardada
  *es* la entrega in-app; por eso `inapp` aparece en la política y en la matriz de preferencias,
  pero nunca en el resultado de la resolución de canales.

## Política

La política dice, para cada categoría y cada canal, qué pasa por defecto y si el usuario lo puede
cambiar. Es un dato de entrada: campanita no trae categorías ni canales propios.

```json
{
  "security": {
    "inapp": { "default": true, "locked": true },
    "email": { "default": true, "locked": true }
  },
  "activity": {
    "inapp": { "default": true, "locked": true },
    "email": { "default": false }
  }
}
```

Cada **celda** tiene:

- `default` (booleano, obligatorio): el valor cuando el usuario nunca tocó la celda.
- `locked` (booleano, opcional, por omisión `false`): el usuario no la puede cambiar.
- `urgent` (booleano, opcional, por omisión `false`): se entrega aunque la persona esté en su
  horario de [no molestar](#no-molestar).

Una política es **válida** cuando se cumple todo esto:

1. Es un objeto con al menos una categoría.
2. Cada clave de categoría y de canal es un identificador.
3. Cada categoría es un objeto de celdas, y cada celda es un objeto que sólo tiene `default` y,
   opcionalmente, `locked` y `urgent`, todos booleanos.
4. Cada categoría incluye `inapp` con `default: true` y `locked: true`. En `inapp`, `urgent` no
   cambia nada: la bandeja recibe siempre.

La regla 4 es la primera invariante del dominio: **la bandeja siempre recibe**. No es un canal
opcional, es donde la notificación vive.

La segunda invariante es una decisión de quien escribe la política y no del spec: marcar como
`locked` los canales de las categorías que no se pueden silenciar (típicamente seguridad). El
spec garantiza que una celda `locked` se respeta en todos lados.

Un port rechaza una política inválida al cargarla, antes de resolver nada.

## Overrides y canales disponibles

- Los **overrides** son las celdas que el usuario cambió, con la misma forma que la política pero
  con un booleano por celda: `{ "activity": { "email": true } }`. Se guardan sólo los cambios: un
  usuario que nunca tocó nada no tiene overrides y recibe los valores por defecto. Agregar una
  categoría o un canal a la política no requiere migrar datos.
- Un override sobre una categoría o un canal que la política no tiene se **ignora**. Es lo que
  queda cuando se saca algo de la política, y no es un error.
- Los **canales disponibles** son los que la instalación tiene configurados para entregar. Un
  canal puede estar en la política y no estar disponible todavía.

## Resolución de canales

`resolve_channels(política, categoría, overrides, disponibles)` devuelve el conjunto de canales
de entrega por los que sale un aviso de esa categoría.

```text
si la categoría no está en la política: devolver vacío

para cada (canal, celda) de la categoría:
    si canal == "inapp":            saltar
    si canal no está en disponibles: saltar
    habilitado = celda.default            si celda.locked
                 override, o celda.default si no hay override
    si habilitado: agregar canal

devolver el conjunto
```

- **Nunca devuelve `inapp`.**
- **Una celda `locked` ignora el override.** Un override viejo en contra no apaga un canal
  bloqueado.
- **Un canal no disponible se excluye**, aunque esté bloqueado en `true`. Encender un canal es
  configurar la instalación, no tocar esta lógica.
- El resultado es un conjunto. Cuando un port lo expone como secuencia, la ordena
  alfabéticamente. Los vectores lo guardan así.

## Matriz efectiva

`effective_matrix(política, overrides, disponibles)` devuelve lo que muestra una pantalla de
preferencias: para cada celda de la política, `enabled` y `locked`.

```text
enabled = celda.default             si celda.locked
          override, o celda.default si no

locked  = celda.locked
          o (canal != "inapp" y canal no está en disponibles)
```

- La interfaz no recalcula la política: la muestra. Por eso `locked` viaja en la respuesta.
- Un canal no disponible se informa como `locked` y conserva su `enabled`. Ofrecer un control que
  no hace nada es peor que decir que todavía no está.
- `inapp` no depende de los canales disponibles.

## Cambio de preferencias

`validate_patch(política, patch, disponibles)` recibe las celdas que el usuario quiere cambiar,
con la forma de los overrides, y devuelve la lista de cambios a guardar o rechaza el pedido.

```text
para cada categoría del pedido, en orden:
    si no está en la política: rechazar (unknown_category, sin canal)
    para cada (canal, valor) de esa categoría, en orden:
        si el canal no está en la categoría: rechazar (unknown_channel)
        si el valor no es un booleano:       rechazar (invalid_value)
        si la celda está bloqueada:          rechazar (locked)
        si el canal no está disponible:      rechazar (unavailable)
        anotar el cambio

devolver los cambios
```

| Motivo | Cuándo |
|---|---|
| `unknown_category` | la categoría no está en la política |
| `unknown_channel` | el canal no está en esa categoría |
| `invalid_value` | el valor no es un booleano |
| `locked` | la celda está bloqueada |
| `unavailable` | el canal no está disponible |

- **El orden es por punto de código Unicode**, primero de categoría y después de canal. Fija qué
  error se informa cuando hay más de uno. Ojo en los lenguajes que ordenan texto por unidades
  UTF-16: dan otro resultado fuera del plano básico.
- Una categoría desconocida se rechaza aunque no traiga celdas, y el error no lleva canal.
- `inapp` está siempre bloqueado, así que pedir cambiarlo da `locked`.
- La forma del pedido (un objeto de objetos) la valida quien lo recibe; el spec empieza cuando ya
  tiene esa forma.

- **Un pedido inválido se rechaza entero y no se guarda nada.** Una celda bloqueada no se ignora
  en silencio: si una interfaz la mandó, tiene un error y conviene que se entere.
- Un pedido vacío es válido y no produce cambios.
- Los cambios se devuelven en el mismo orden en que se revisan.
- Un cambio que deja la celda en su valor por defecto es válido y se guarda igual: el override
  explícito sobrevive a un cambio posterior del valor por defecto.

## Plantillas

Una plantilla es texto con variables. Es a propósito lo mínimo, para que lo que una plantilla
puede hacer sea igual en todos los lenguajes.

- `{nombre}` se reemplaza por el valor de `nombre` en el payload. El nombre cumple
  `^[a-z][a-z0-9_]{0,63}$`.
- `{{` y `}}` son una llave literal.
- Cualquier otra `{` o `}` hace inválida la plantilla. No hay formatos, expresiones ni acceso a
  propiedades.

`render_template(plantilla, payload)`:

1. **Revisa la plantilla entera antes de mirar el payload.** Una plantilla inválida falla con
   `invalid_template` aunque antes le falte una variable.
2. Reemplaza las variables en orden de aparición. La primera que no se puede resolver corta:

| Motivo | Cuándo |
|---|---|
| `missing_variable` | el payload no tiene esa clave como propia |
| `invalid_variable` | el valor no es texto ni un entero mostrable |

- **Texto** se inserta tal cual. No se vuelve a interpretar: un valor `{b}` queda `{b}`.
- **Un entero** entre `-(2^53 - 1)` y `2^53 - 1` se escribe en base 10. Un número sin parte
  fraccionaria cuenta como entero (`3.0` es `3`, `-0.0` es `0`), porque JSON no distingue.
- Todo lo demás es `invalid_variable`: decimales, enteros fuera de ese rango, booleanos, nulos,
  listas y objetos.
- Las claves del payload que la plantilla no usa se ignoran.
- "Como propia" importa en los lenguajes donde los objetos heredan propiedades: `{constructor}`
  falta en un payload vacío.

## Catálogo

El catálogo dice qué tipos de aviso existen. Quien emite manda un tipo y un payload, y no sabe de
categorías ni de textos: eso se decide en un solo lugar. Como la política, es un dato de entrada.

```json
{
  "invitations.received": {
    "category": "invitations",
    "title": "Te invitaron a {project}",
    "body": "{inviter} te invitó al proyecto {project}."
  }
}
```

Un catálogo es **válido** contra una política cuando:

1. Es un objeto. Puede estar vacío.
2. Cada clave es un **identificador de tipo**: segmentos separados por puntos, cada uno con la
   forma `[a-z][a-z0-9_-]*`, y hasta 128 caracteres en total.
3. Cada entrada es un objeto que sólo tiene `category`, `title` y, opcionalmente, `body` y
   `audience`.
4. `category` es una categoría de la política.
5. `title` es una plantilla válida y no vacía. `body`, si está, es una plantilla válida.
6. `audience`, si está, es `person` o `group`.

Un port rechaza un catálogo inválido al cargarlo, antes de armar ningún aviso.

### Audiencia

`audience` dice a quién le habla un tipo:

- **`person`** (o sin `audience`): a una persona. El aviso sale por los canales que esa persona
  eligió, a sus destinos.
- **`group`**: a un grupo, como un equipo o un proyecto. El `recipient_id` es el del grupo y el
  aviso sale **una sola vez**, por los canales y a los destinos del grupo, no por los de cada
  miembro. A los miembros les queda una copia en su bandeja (ver
  [el recorrido completo](#el-recorrido-completo)).

Qué es un grupo y quiénes lo forman lo decide quien integra: campanita no guarda grupos ni
miembros. Las preferencias y el horario de no molestar de un grupo son los guardados con su
`recipient_id`, igual que los de una persona.

Un mismo hecho puede interesarle a una persona y a un grupo: son dos tipos, uno de cada
audiencia, y quien emite elige cuál manda o manda los dos.

## Armado del aviso

`build_notification(catálogo, evento)` convierte un evento en un aviso listo para guardar, o lo
rechaza.

El **evento** es lo que manda quien emite:

| Campo | Forma |
|---|---|
| `type` | un tipo del catálogo |
| `recipient_id` | texto no vacío. Es opaco: campanita no lo interpreta |
| `payload` | un objeto; si falta, vale `{}` |
| `action_url` | texto no vacío, nulo o ausente |
| `scope` | texto no vacío, nulo o ausente |
| `dedupe_key` | texto no vacío, nulo o ausente |

Las revisiones van en este orden, y la primera que falla corta:

| Paso | Motivo | `detail` |
|---|---|---|
| 1. El tipo es texto y está en el catálogo | `unknown_type` | el tipo, o nulo si no es texto |
| 2. `recipient_id`, `payload`, `action_url`, `scope`, `dedupe_key`, en ese orden | `invalid_field` | el campo |
| 3. No hay campos desconocidos | `invalid_field` | el primero por punto de código |
| 4. Se arma el título | `missing_variable`, `invalid_variable` | la variable |
| 5. El título no queda vacío | `empty_title` | nulo |
| 6. Se arma el cuerpo, si el tipo lo tiene | `missing_variable`, `invalid_variable` | la variable |

- **Un campo desconocido se rechaza.** Un `dedup_key` mal escrito no puede perderse en silencio y
  dejar pasar duplicados.
- **Vacío** quiere decir sin ningún carácter que no sea espacio (U+0020), tabulación, salto de
  línea o retorno de carro. La lista es fija porque "espacio" no significa lo mismo en cada
  lenguaje.
- Un texto vacío en `action_url`, `scope` o `dedupe_key` se rechaza: "no hay" se dice con nulo o
  dejando el campo afuera, de una sola forma.

El **aviso armado** tiene `recipient_id`, `type`, `category` (la del catálogo), `title`, `body`
(nulo si el tipo no tiene cuerpo), `action_url`, `payload`, `scope` y `dedupe_key`, con los
opcionales ausentes en nulo.

- **No tiene identificador ni fecha.** Los pone quien guarda.
- **El payload se guarda entero**, también lo que las plantillas no usaron: permite volver a
  redactar el aviso más adelante sin pedirle el evento a quien lo emitió.
- **`action_url` no se interpreta.** Quien la muestre como enlace tiene que revisar su esquema:
  el spec no impide un `javascript:`.

## La bandeja

La bandeja es lo que se guarda y se lee de un **almacén**. El spec no dice sobre qué base está
hecho; dice qué tiene que contestar. Un almacén cumple cuando reproduce, paso por paso, las
secuencias de `vectors/bandeja/`.

Un **aviso guardado** es un aviso armado más tres campos:

- `id`: texto. Lo provee quien llama y el almacén no lo interpreta.
- `created_at`: la fecha en que se guardó. La provee quien llama al guardar, nunca quien emite
  el evento.
- `read_at`: nulo mientras no se leyó. Es lo único que cambia después de creado.

Las fechas tienen resolución de milisegundos. Los vectores las escriben como milisegundos desde
1970 (`created_at_ms`, `read_at_ms`).

Dos reglas valen para todas las operaciones:

- **Todo pide el destinatario.** Un aviso sólo lo ve y lo cambia su `recipient_id`, comparado
  como texto exacto. Con el destinatario equivocado, un aviso se comporta como si no existiera.
- **El ámbito es un filtro, no un permiso.** Con un ámbito, entran los avisos de ese ámbito y los
  que no tienen ámbito (los avisos de cuenta se ven siempre). Sin ámbito, entran todos.

### Guardar

`add(aviso, id, created_at)` devuelve el aviso guardado y si se creó.

```text
si el aviso tiene dedupe_key y ya hay uno con el mismo recipient_id y dedupe_key:
    devolver ese aviso, creado = falso       no se escribe nada
si ya hay un aviso con ese id:
    rechazar con duplicate_id
guardar con read_at nulo y devolverlo, creado = verdadero
```

- **La clave es por persona.** La misma clave para otra persona crea otro aviso; la misma clave
  en otro ámbito, no.
- **El repetido no pisa nada.** Vuelve el aviso original tal como está, también si ya fue leído.
  El `id` y el contenido del repetido se descartan.
- **Sin clave, nada es un repetido.** Dos avisos idénticos sin clave son dos avisos.
- **Revisar la clave y guardar es una sola operación.** Dos pedidos simultáneos con la misma
  clave dejan un solo aviso. Los vectores no pueden probar la simultaneidad: es responsabilidad
  de cada adaptador.

### Listar

`list(consulta)` devuelve una página y el cursor de la siguiente.

| Campo de la consulta | Efecto |
|---|---|
| `recipient_id` | obligatorio |
| `limit` | obligatorio; cuántos avisos como máximo |
| `cursor` | desde dónde seguir; ausente o nulo, desde el principio |
| `unread_only` | sólo los que tienen `read_at` nulo |
| `category` | sólo los de esa categoría |
| `scope` | el filtro de ámbito |

- **El orden es del más nuevo al más viejo**: por `created_at` descendente y, a igual fecha, por
  `id` descendente comparado por punto de código Unicode. En UTF-8 eso es el orden de los bytes.
- **El cursor es la posición del último aviso de la página**: su `created_at` y su `id`. La
  página siguiente trae lo que está estrictamente después de esa posición en el orden. El cursor
  no necesita corresponder a un aviso que exista.
- **Los filtros se aplican antes de cortar la página.**
- **`next_cursor` es nulo cuando no queda nada más**, también si la página salió completa.
- Un aviso que llega entre una página y la siguiente no hace repetir ni saltear ninguno de los
  anteriores.
- `limit` tiene que ser un entero mayor que cero; si no, se rechaza con `invalid_limit`. El spec
  no fija un máximo.

### Contar los no leídos

`count_unread(recipient_id, scope, cap)` devuelve cuántos avisos visibles tienen `read_at` nulo.

- Con `cap`, el resultado es el menor entre la cuenta y `cap`. Sirve a una interfaz que muestra
  "99+": no necesita el total exacto, y el almacén puede dejar de contar ahí.
- `cap` es nulo o un entero mayor que cero; si no, se rechaza con `invalid_cap`.

### Marcar como leído

`mark_read(id, recipient_id, read_at)` devuelve verdadero sólo si el aviso pasó de no leído a
leído.

- Si el aviso no existe, es de otra persona o ya estaba leído, devuelve falso y no cambia nada.
- **La hora de lectura es la primera.** Marcar de nuevo no la mueve.

`mark_all_read(recipient_id, before, read_at, scope, category)` marca los avisos visibles, no
leídos, con `created_at` **estrictamente anterior** a `before`, y devuelve cuántos cambió.

- `before` es hasta dónde vio la persona. El corte estricto evita marcar como leído un aviso que
  llegó en el mismo instante y que la persona no llegó a ver.
- Los que ya estaban leídos conservan su hora.

## Las preferencias guardadas

Lo que cambió cada persona se guarda en un **almacén de preferencias**. Igual que con la bandeja,
el spec no dice sobre qué base está hecho: un almacén cumple cuando reproduce las secuencias de
`vectors/preferencias-guardadas/`.

Lo guardado tiene la forma de los overrides: categoría → canal → booleano.

- `get_overrides(recipient_id, scope)` devuelve lo guardado para esa persona en ese ámbito. Quien
  nunca cambió nada no tiene nada guardado, y la respuesta es vacía.
- `save_changes(recipient_id, cambios, scope)` guarda una lista de cambios, cada uno con
  `category`, `channel` y `enabled`.

Reglas:

- **Sólo se guardan los cambios.** Sin nada guardado valen los valores de la política, así que
  agregarle una categoría o un canal no requiere migrar datos.
- **El almacén no conoce la política.** Guarda la celda que le pasen. Qué se puede cambiar lo
  decide `validate_patch`, antes de guardar.
- **Guardar una celda que ya estaba la reemplaza.** Si un mismo pedido repite una celda, vale la
  última.
- **Un pedido se guarda entero o no se guarda.** Un pedido sin cambios no hace nada.
- **Cada ámbito es independiente.** Lo guardado sin ámbito no aparece al pedir un ámbito, ni al
  revés: acá no hay herencia. "Sin ámbito" y un ámbito de texto vacío son cosas distintas.
- **La persona, el ámbito, la categoría y el canal son texto exacto.** Ninguna combinación de
  valores se puede confundir con otra.

Para resolver los canales de un aviso, se le pasa a `resolve_channels` lo que devuelve
`get_overrides` para el destinatario y el ámbito del aviso.

## El recorrido completo

`notify(evento, id, created_at, miembros)` es la operación que usa quien quiere avisarle algo a
alguien. Trabaja con lo que una instalación tiene configurado: una política, un catálogo, un
almacén de avisos, un almacén de preferencias y una lista de canales de entrega.

`miembros` sólo tiene sentido para un tipo de audiencia `group`: es la lista de quienes reciben
una copia en su bandeja, cada uno con el `id` que va a tener su copia. Puede faltar o estar
vacía.

```text
1. aviso = build_notification(catálogo, evento)        si se rechaza, termina: no se guardó nada
2. revisar los miembros                                si se rechaza, termina: no se guardó nada
3. guardado = almacén.add(aviso, id, created_at)       si se rechaza, termina
4. si no se creó (la clave ya existía):
       devolver el aviso que había, sin copias ni entregas
5. para cada miembro, en el orden en que vienen:
       copia = el aviso, con el recipient_id del miembro
       almacén.add(copia, id del miembro, created_at)  si se rechaza, termina
6. overrides = preferencias.get_overrides(recipient_id, ámbito del aviso)
7. canales = resolve_channels(política, categoría, overrides, canales configurados)
8. en_silencio = el destinatario está ahora en su horario de no molestar
9. para cada canal, en orden por punto de código:
       si en_silencio y la celda no es `urgent`: anotar `silenced`, sin intentar la entrega
       si no: intentar la entrega y anotar el resultado
10. devolver el aviso, que se creó, las copias y el resultado de cada canal
```

Los miembros se rechazan con `invalid_field` y `members` de detalle cuando:

- el tipo es de audiencia `person` y la lista no está vacía;
- un miembro no es un objeto con exactamente `recipient_id` e `id`, los dos texto no vacío;
- dos miembros tienen el mismo `recipient_id` o el mismo `id`.

Reglas de las copias:

- **Una copia es un aviso más en la bandeja del miembro**: mismo tipo, categoría, textos,
  `action_url`, payload, ámbito y `dedupe_key`, y la misma fecha. Se lista, se cuenta y se marca
  como leída como cualquier otro.
- **Una copia no se entrega.** El aviso ya salió por los canales del grupo; mandarlo además a
  cada miembro es lo que la audiencia `group` evita. Por eso no se miran las preferencias ni el
  horario de los miembros.
- **La clave vale por persona, como siempre.** Si un miembro ya tenía un aviso con esa
  `dedupe_key`, su copia no se crea y se informa como no creada.
- **Si el aviso del grupo ya existía, no se hacen copias**: el repetido no hace nada.
- **Si el almacén rechaza una copia, el recorrido termina con ese error**: lo guardado hasta ahí
  queda y no se entrega nada. Un `id` repetido es un error de quien llama.

- **Primero se guarda, después se entrega.** La bandeja es la fuente de verdad: una entrega que
  falla nunca deshace el aviso ni sus copias.
- **Un repetido no se vuelve a entregar**, ni siquiera si la primera entrega falló. Reintentar es
  decisión de quien llama.
- **Las preferencias que valen son las del ámbito del aviso.** Un aviso sin ámbito usa lo
  guardado sin ámbito.
- **Los canales disponibles son los configurados.** Un canal que la política nombra pero la
  instalación no tiene no se intenta, aunque esté bloqueado en encendido.
- **Cada canal se intenta por separado.** Que uno falle no impide intentar los demás.

### Canales de entrega

Un canal recibe el aviso guardado y contesta uno de cuatro resultados:

| Resultado | Qué significa |
|---|---|
| `sent` | el proveedor aceptó el aviso |
| `expired` | el destino ya no existe y el adaptador lo tiene que olvidar |
| `failed` | no se pudo entregar |
| `skipped` | no había a dónde entregar; reintentar no sirve |

Un canal que lanza un error, o que contesta algo que no es uno de esos cuatro, cuenta como
`failed`.

Hay un quinto resultado, `silenced`, que no lo contesta un canal sino el recorrido: el canal no
se intentó porque la persona está en su horario de [no molestar](#no-molestar). Un canal que
contesta `silenced` cuenta como `failed`.

Cada canal resuelve sus propios destinos a partir del aviso: a qué dirección o a qué
dispositivos va no pasa por el núcleo. El destinatario y el ámbito viajan en el aviso, así que un
canal puede tener un destino distinto para cada ámbito de una misma persona o grupo.

Los canales configurados son **válidos** cuando cada identificador cumple la forma de
identificador, ninguno es `inapp` y ninguno se repite. Puede no haber ninguno: entonces los
avisos se guardan y no se entregan.

## No molestar

Cada persona puede definir un horario en el que los canales de entrega no la interrumpen. La
bandeja no se entera: el aviso se guarda igual y está ahí cuando la persona mire.

```json
{ "enabled": true, "start": "22:00", "end": "08:00", "timezone": "America/Lima" }
```

Un horario es **válido** cuando es un objeto que tiene exactamente estas cuatro claves:

- `enabled`: booleano. Apagado, el horario queda guardado pero no silencia nada.
- `start` y `end`: texto `HH:MM`, de `00:00` a `23:59`, siempre con dos dígitos y dos dígitos.
- `timezone`: texto de 1 a 64 caracteres entre letras ASCII, dígitos y `_ + - /`. Es el nombre
  de la zona horaria de la persona. El núcleo no lo interpreta.

`is_quiet(horario, minuto)` dice si un momento cae dentro del horario. `minuto` es el minuto del
día en la hora local de la persona, de 0 a 1439.

- Con `enabled` en `false`, nunca.
- Si `start` es menor que `end`, desde `start` inclusive hasta `end` exclusive.
- Si `start` es mayor que `end`, el horario cruza la medianoche: desde `start` hasta el final del
  día, y desde `00:00` hasta `end` exclusive.
- Si son iguales, el horario está vacío: nunca.

Reglas del recorrido:

- **El horario es del destinatario, no de un ámbito.** Vale para todos sus avisos. El de un grupo
  es el guardado con su `recipient_id`.
- **Quien nunca definió un horario no tiene ninguno.** Y una instalación sin almacén de horarios
  no silencia nada.
- **Una celda `urgent` se entrega igual.** Qué es urgente lo decide quien escribe la política.
- **Lo silenciado no se entrega después.** El resultado queda anotado como `silenced`; juntar lo
  silenciado y mandarlo más tarde es decisión de quien integra.
- **Pasar del instante al minuto local es asunto de cada port**, con la base de zonas horarias
  de su plataforma: es lo único de esta sección que los vectores no cubren. Si la plataforma no
  conoce la zona, la persona no está en silencio: ante la duda, se entrega.

El horario se guarda en un **almacén de horarios**, que cumple cuando reproduce las secuencias de
`vectors/silencio-guardado/`:

- `get_quiet_hours(recipient_id)` devuelve el horario de esa persona, o nada.
- `set_quiet_hours(recipient_id, horario)` lo guarda, reemplazando el que hubiera.

El almacén guarda el horario que le pasen, sin validarlo, y la persona es texto exacto.

## Email

El canal de email separa dos cosas: qué se le dice a la persona y con qué se manda. El spec cubre
la primera, que es igual en todos los lenguajes; el transporte (SMTP, un servicio por HTTP) es
asunto de cada adaptador.

### La dirección

El canal no guarda direcciones: le pregunta a quien integra cuál es la de un destinatario.

Una dirección es **válida** cuando:

1. Es texto, de 3 a 254 caracteres.
2. Sólo tiene caracteres ASCII visibles (de `!` a `~`): sin espacios, sin caracteres de control
   y sin letras fuera de ASCII.
3. No tiene ninguno de `< > , ; : " ( ) [ ] \`.
4. Tiene exactamente una `@`, con algo antes y algo después.

La regla es más estricta que la del correo a propósito: una dirección que pasa no puede partir
una cabecera ni esconder un segundo destinatario. Deja afuera las direcciones con letras no ASCII
y las que llevan comillas.

Si el destinatario no tiene dirección, o la que tiene no es válida, el canal contesta `skipped` y
el transporte no se llama.

### El mensaje

`render_email(aviso)` arma el mensaje por omisión:

- **Asunto**: el título, con cada tramo de caracteres de control (U+0000 a U+001F y U+007F)
  convertido en un espacio. Un salto de línea en el título no puede terminar la cabecera.
- **Texto**: el cuerpo y la `action_url`, separados por una línea en blanco, con los que haya. Un
  cuerpo vacío es como no tener cuerpo. Si no hay ninguno de los dos, el texto es el título, sin
  tocar.

Quien integra puede reemplazar esta función. Aun así, el asunto que sale siempre pasa por la
misma limpieza de caracteres de control.

## Vectores

Cada archivo de `vectors/` cumple `schemas/vector.schema.json` y tiene `spec`, `id`, `kind` y sus
casos.

| `kind` | Qué prueba | Cada caso |
|---|---|---|
| `politica` | validación de la política | `policy`, `valid` |
| `canales` | resolución de canales | `category`, `overrides`, `available`, `expect` |
| `matriz` | matriz efectiva | `overrides`, `available`, `expect` |
| `preferencias` | cambio de preferencias | `patch`, `available`, `expect` |
| `plantilla` | plantillas | `template`, `payload`, `expect` |
| `catalogo` | validación del catálogo | `catalog`, `valid` |
| `aviso` | armado del aviso | `event`, `expect` |
| `bandeja` | la bandeja sobre un almacén | secuencia de pasos (ver abajo) |
| `preferencias-guardadas` | las preferencias sobre un almacén | secuencia de pasos (ver abajo) |
| `recorrido` | el recorrido completo | secuencia de pasos (ver abajo) |
| `recorrido-canales` | validación de los canales configurados | `channels`, `valid` |
| `silencio-validacion` | validez de un horario de no molestar | `setting`, `valid` |
| `silencio` | si un minuto cae en el horario | `setting`, `minute`, `expect` |
| `silencio-guardado` | los horarios sobre un almacén | secuencia de pasos (ver abajo) |
| `email-mensaje` | el mensaje por omisión | `notification`, `expect` |
| `email-direcciones` | validez de una dirección | `address`, `valid` |

Los vectores de `canales`, `matriz`, `preferencias` y `catalogo` llevan la `policy` una vez por
archivo; los de `aviso` llevan además el `catalog`.

Los vectores de `bandeja`, `preferencias-guardadas`, `silencio-guardado` y `recorrido` no tienen
`cases` sino `steps`: una secuencia
que se corre en orden sobre un almacén vacío. Cada paso tiene `op`, sus argumentos y `expect`,
que es lo que la operación devuelve, o `{"error": motivo}` cuando la rechaza.

- En `bandeja`, `op` es `add`, `list`, `count_unread`, `mark_read` o `mark_all_read`.
- En `preferencias-guardadas`, `op` es `save` (devuelve nulo) o `get`.
- En `silencio-guardado`, `op` es `set` (devuelve nulo) o `get` (devuelve el horario o nulo).
- En `recorrido`, el vector trae la `policy`, el `catalog` y los `channels` configurados, y `op` es
  `notify` o `save` (guardar preferencias). Cada `notify` lleva `outcomes`: qué contesta cada
  canal en ese paso (por omisión, `sent`). Además de los cuatro resultados, `throws` hace que el
  canal lance un error; `garbage`, que conteste algo que no es un resultado; y `unknown`, que
  conteste un resultado con un valor que no existe. `op` también puede ser `quiet` (guardar el
  horario de no molestar de una persona), y entonces cada `notify` puede traer `local_minute`: el
  minuto local que el recorrido tiene que usar para el destinatario en ese paso. Un `notify`
  también puede traer `members`, la lista que recibe el recorrido. `expect` tiene `created`,
  `notification`, `copies` (cada una con `created` y `notification`, en el orden de los
  miembros), `attempted` (los canales intentados, en orden) y `deliveries`, o
  `{"error": {"reason", "detail"}}`.

`python3 tools/gen_vectors.py` los regenera; con `--check` falla si los archivos del repo no
coinciden con lo generado.
