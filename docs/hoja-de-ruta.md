# Hoja de ruta

## Hecho

- Política, resolución de canales, matriz efectiva y cambio de preferencias.
- Plantillas, catálogo de tipos y armado del aviso a partir de un evento.
- La bandeja: guardar sin duplicar, paginar por cursor, contar los no leídos y marcarlos como
  leídos, especificada como secuencias que cualquier base tiene que reproducir.
- Las preferencias guardadas, especificadas de la misma forma.
- El recorrido completo: una sola operación que arma el aviso, lo guarda y lo entrega por los
  canales que corresponden.
- El canal de email, con transporte intercambiable: SMTP (Python), HTTP genérico y Resend.
- No molestar: un horario por persona en el que los canales no interrumpen, salvo lo que la
  política marca como urgente.
- Almacenes incluidos, para avisos, preferencias y horarios: en memoria (Python y TypeScript), SQLite
  (Python) y Cloudflare D1 (TypeScript).
- Vectores de conformidad, su generador independiente y un corredor de secuencias para quien
  escriba un adaptador.
- Núcleo en Python y en TypeScript, sin dependencias, pasando los mismos vectores.
- Publicado en npm y PyPI, con la misma versión en los dos.

## Lo que sigue

1. **Web push**, y después push nativo. Es el segundo canal, el que termina de fijar el puerto
   de entrega.
2. **Registro y reintento de entregas.** Guardar qué pasó con cada entrega y volver a intentar
   las que fallaron.
3. **Más de email.** SMTP en TypeScript, mensajes en HTML y qué hacer con los rebotes.
4. **Más almacenes.** Postgres, en los dos lenguajes.
5. **La campanita.** Componentes para mostrar la bandeja y la pantalla de preferencias.
6. **Un servicio listo para desplegar.** Una API HTTP armada con el núcleo y los adaptadores,
   para quien no quiera embeber la librería.
7. **Avisos en varios idiomas.** El payload ya se guarda entero para poder redactar de nuevo.
8. **Vencimiento y borrado** de avisos viejos.
9. **Restablecer preferencias**: volver una celda al valor de la política.
10. **Entregar después lo silenciado**: un resumen de lo que no salió durante el horario de no
    molestar. Depende del registro de entregas.

## Fuera de alcance

- Flujos con pasos y esperas, y un editor visual de plantillas.
- Un catálogo propio de proveedores: cada canal es un adaptador que escribe quien lo necesita.
- Autenticación y autorización: el núcleo recibe datos ya autorizados.
