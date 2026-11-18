# Principios

Lo que hace útil a campanita es que no sabe nada de quien la usa. Estas reglas existen para que
siga así.

## 1. El núcleo no conoce a nadie

Ni el spec ni el núcleo nombran un producto, una empresa, un proveedor ni una base. Tampoco traen
categorías, canales o tipos de aviso propios: los que aparecen en los ejemplos y en los vectores
son datos de prueba.

Una forma de comprobarlo: cada regla del spec se tiene que poder explicar sin mencionar ningún
sistema en particular. Si para justificarla hace falta decir "porque en tal producto…", no es una
regla del spec.

Un **adaptador** sí nombra lo que adapta, porque para eso existe: un almacén sobre una base, un
transporte para un protocolo o para un servicio público. Vive en su propio módulo, fuera del
núcleo, y nada del núcleo lo importa. Lo que un adaptador no nombra nunca es el sistema de quien
lo usa: un servicio propio se conecta configurando un adaptador genérico, desde afuera.

## 2. Lo particular entra como dato o como adaptador

Lo que cambia de un producto a otro tiene dos lugares, y ninguno es el núcleo:

- **Datos**: la política (qué sale por dónde y qué se puede apagar) y el catálogo (qué tipos de
  aviso hay y cómo se redactan).
- **Adaptadores**: la base donde se guarda, los canales por los que se entrega y la superficie
  que muestra la bandeja.

Si una necesidad no se puede resolver con datos ni con un adaptador, antes de tocar el núcleo
vale preguntarse si la necesidad es general o es de un caso.

## 3. Sin valores por defecto que sean decisiones de producto

Cuántos avisos trae una página, hasta dónde se cuenta, cuánto dura un aviso: eso lo decide quien
integra. El núcleo pide el valor o no lo usa, pero no elige uno.

## 4. Lo opaco se queda opaco

El identificador de una persona, el ámbito de un aviso y la dirección a la que lleva son texto
que campanita guarda y devuelve sin interpretar. No asume que un identificador es un número, que
un ámbito es una organización ni que una dirección es una URL.

## 5. Un comportamiento, muchos lenguajes

Todo lo que el núcleo hace está en el spec y tiene vectores. Un comportamiento que sólo existe en
un paquete es un error, aunque sea cómodo. Por eso el spec evita lo que cada lenguaje resuelve
distinto por su cuenta: qué es un espacio, cómo se ordena un texto, cómo se escribe un número.

## 6. Un puerto se gana con dos adaptadores

Un puerto con un solo adaptador tiene la forma de ese adaptador. No se da por estable hasta
tener dos, hechos para sistemas distintos.

## 7. Chico y legible

Sin dependencias de ejecución. Quien adopta campanita tiene que poder leer el núcleo entero en
una tarde. Lo que no entra en esa medida va a un adaptador o a otro proyecto.
