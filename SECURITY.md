# Política de seguridad

## Versiones con soporte

El proyecto es un borrador y todavía no tiene versiones publicadas. Cuando las tenga, sólo la
última va a recibir correcciones.

## Cómo reportar una vulnerabilidad

No abras un issue público por un problema de seguridad. Usá el reporte privado de GitHub:

https://github.com/PabloAlaniz/campanita/security/advisories/new

Vas a tener una respuesta en unos días. Cuando salga la corrección, el reporte se acredita salvo
que prefieras lo contrario.

## Qué entra

- Que un canal bloqueado por la política deje de salir por algo que hizo el usuario.
- Que un cambio de preferencias rechazado se guarde igual, entero o en parte.
- Que una política inválida se acepte.
- Que un nombre de categoría o de canal elegido por un atacante altere el resultado de otros.

## Qué no entra

- Una política que no bloquea lo que debería: qué se puede silenciar lo decide quien la escribe.
- La autenticación y la autorización de quien llama. El núcleo no las hace: recibe datos ya
  autorizados.
