/** Base64 para los transportes que firman o cifran. Sin dependencias: `btoa` y `atob`. */

export function toBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Acepta los dos alfabetos (el común y el de URL), con o sin relleno. Rechaza lo que no es base64. */
export function fromBase64(text: string): Uint8Array<ArrayBuffer> {
  const binary = atob(text.replace(/-/g, '+').replace(/_/g, '/').replace(/=+$/, ''));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const UTF8 = new TextEncoder();

export const utf8 = (text: string): Uint8Array<ArrayBuffer> => UTF8.encode(text) as Uint8Array<ArrayBuffer>;

/** Un segmento de un JWT: el JSON, en base64 de URL. */
export const jsonSegment = (value: unknown): string => toBase64Url(utf8(JSON.stringify(value)));

/** Une tramos de bytes en uno solo. */
export function concat(...parts: readonly Uint8Array[]): Uint8Array<ArrayBuffer> {
  const joined = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    joined.set(part, offset);
    offset += part.length;
  }
  return joined;
}

/** Una clave de Web Crypto, sin depender de los tipos de un entorno en particular. */
export type Key = Awaited<ReturnType<typeof crypto.subtle.importKey>>;
