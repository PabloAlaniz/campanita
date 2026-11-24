# Casos compartidos de los adaptadores

Lo que un adaptador hace igual en todos los lenguajes y no forma parte del spec, porque nombra lo
que adapta. Cada archivo se escribe a mano y lo leen los tests de los dos paquetes: si uno de los
dos se aparta, falla.

- [`push.json`](push.json): el mensaje por omisión del canal de push y cómo se resume un envío a
  varios dispositivos.
- [`slack.json`](slack.json): el mensaje por omisión del canal de Slack, qué URL se acepta como
  webhook y cómo se lee la respuesta de la conexión.
