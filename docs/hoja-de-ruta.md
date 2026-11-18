# Hoja de ruta

## Hecho

- Política, resolución de canales, matriz efectiva y cambio de preferencias.
- Plantillas, catálogo de tipos y armado del aviso a partir de un evento.
- La bandeja: guardar sin duplicar, paginar por cursor, contar los no leídos y marcarlos como
  leídos, especificada como secuencias que cualquier base tiene que reproducir.
- Las preferencias guardadas, especificadas de la misma forma.
- Almacenes incluidos, para avisos y para preferencias: en memoria (Python y TypeScript) y SQLite
  (Python).
- Vectores de conformidad, su generador independiente y un corredor de secuencias para quien
  escriba un adaptador.
- Núcleo en Python y en TypeScript, sin dependencias, pasando los mismos vectores.

## Lo que sigue

1. **Publicar una primera versión** en npm y PyPI.
2. **Las entregas.** Estados de una entrega, reintentos y qué hacer con un destino vencido.
3. **Adaptadores de canal.** Email y web push; después, push nativo.
4. **El recorrido completo.** Una sola operación que arma el aviso, lo guarda y lo entrega por
   los canales que corresponden.
5. **Más almacenes.** Uno sobre SQL en TypeScript y uno para una base con driver asíncrono.
6. **La campanita.** Componentes para mostrar la bandeja y la pantalla de preferencias.
7. **Un servicio listo para desplegar.** Una API HTTP armada con el núcleo y los adaptadores,
   para quien no quiera embeber la librería.
8. **Avisos en varios idiomas.** El payload ya se guarda entero para poder redactar de nuevo.
9. **Vencimiento y borrado** de avisos viejos.
10. **Restablecer preferencias**: volver una celda al valor de la política.

## Fuera de alcance

- Flujos con pasos y esperas, y un editor visual de plantillas.
- Un catálogo propio de proveedores: cada canal es un adaptador que escribe quien lo necesita.
- Autenticación y autorización: el núcleo recibe datos ya autorizados.
