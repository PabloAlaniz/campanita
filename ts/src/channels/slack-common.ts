/** Lo de Slack que usan el canal y la conexión por OAuth. */

const WEBHOOK = /^https:\/\/hooks\.slack\.com\/services\/[A-Za-z0-9/_-]{1,200}$/;
/** Los errores de Slack son una palabra en minúsculas: sólo eso se repite. */
export const SLACK_ERROR = /^[a-z0-9_]{1,60}$/;

/**
 * ¿Es la URL de un webhook de Slack?
 *
 * Estricta a propósito: sólo `https://hooks.slack.com/services/…`, sin
 * usuario, puerto, consulta ni fragmento. Así una URL guardada no sirve para
 * hacer pedidos a otro servidor.
 */
export function isSlackWebhookUrl(url: unknown): url is string {
  return typeof url === 'string' && WEBHOOK.test(url);
}
