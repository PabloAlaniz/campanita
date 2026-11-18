# Puertos

El núcleo de campanita decide; no guarda, no envía y no dibuja. Todo eso entra por tres puertos.
Están declarados en [`python/src/campanita/ports.py`](../python/src/campanita/ports.py) y en
[`ts/src/ports.ts`](../ts/src/ports.ts).

> **Son un borrador.** El spec v0 cubre sólo la política, la resolución de canales y las
> preferencias. Los puertos fijan la forma, pero pueden cambiar hasta que el spec v1 los cubra.

## Los tres puertos

| Puerto | Qué resuelve | Ejemplos de adaptador |
|---|---|---|
| `NotificationStore` | Guardar avisos y leer la bandeja | Postgres, SQLite, D1 |
| `PreferenceStore` | Guardar lo que cambió cada persona | La misma base, u otra |
| `DeliveryChannel` | Entregar un aviso fuera de la bandeja | Email, web push, APNs |

La **bandeja** no es un puerto: es lo que se lee del `NotificationStore`. Cualquier superficie
que la muestre (un panel web, una app, un bot) es un cliente de esa lectura.

## Decisiones que ya están tomadas

- **Toda lectura pide el destinatario.** Es el único alcance de seguridad, y por eso es un
  parámetro obligatorio y no algo que quien llama se tiene que acordar de filtrar.
- **La fecha de creación la pone quien guarda.** La bandeja pagina por `(fecha, id)`; si la
  fecha la pusiera quien emite, un reloj corrido insertaría avisos en una página ya servida.
- **Las preferencias guardan sólo los cambios.** Sin filas, valen los valores de la política.
  Agregar una categoría o un canal no obliga a migrar datos.
- **Un canal de entrega nunca lanza.** Devuelve `sent`, `expired` o `failed`. Un destino roto no
  puede impedir avisarle al resto, y `expired` le dice al adaptador que olvide ese destino.
- **Cada canal resuelve sus destinos.** El núcleo le pasa el aviso; a qué dirección de email o a
  qué suscripciones de push va es asunto del adaptador.

## La regla de los dos adaptadores

Un puerto no se da por estable hasta tener **dos adaptadores reales**. Con uno solo, el puerto
termina teniendo la forma de ese adaptador y el segundo no entra.

Cuando haya adaptadores, cada uno va a tener que pasar una batería de conformidad, igual que el
núcleo pasa los vectores: es lo que permite que cualquiera escriba uno y demuestre que es
correcto.
