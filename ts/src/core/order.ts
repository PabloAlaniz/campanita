/**
 * Orden por punto de código Unicode, como pide el spec para categorías, canales,
 * claves e identificadores. La comparación por defecto de JavaScript usa
 * unidades UTF-16 y da otro resultado fuera del plano básico.
 */
export function byCodePoint(a: string, b: string): number {
  const left = a[Symbol.iterator]();
  const right = b[Symbol.iterator]();
  for (;;) {
    const x = left.next();
    const y = right.next();
    if (x.done || y.done) return (x.done ? 0 : 1) - (y.done ? 0 : 1);
    // Iterar un texto da puntos de código enteros: `codePointAt(0)` siempre existe.
    const diff = (x.value.codePointAt(0) ?? 0) - (y.value.codePointAt(0) ?? 0);
    if (diff !== 0) return diff;
  }
}
