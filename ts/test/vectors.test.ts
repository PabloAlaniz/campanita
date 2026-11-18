/**
 * Corre todos los vectores de `spec/vectors/` contra el paquete.
 *
 * Es la prueba de conformidad: el paquete cumple el spec cuando esto pasa.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  buildNotification,
  effectiveMatrix,
  EventRejectedError,
  InvalidCatalogError,
  InvalidPolicyError,
  InvalidTemplateError,
  parseCatalog,
  parsePolicy,
  PatchRejectedError,
  renderTemplate,
  resolveChannels,
  SPEC_VERSION,
  TemplateError,
  validatePatch,
  type Overrides,
} from '../src/index.js';

const SPEC = fileURLToPath(new URL('../../spec', import.meta.url));
const VECTORS = join(SPEC, 'vectors');

interface Vector {
  spec: string;
  id: string;
  kind: 'politica' | 'canales' | 'matriz' | 'preferencias' | 'plantilla' | 'catalogo' | 'aviso' | 'bandeja';
  policy?: unknown;
  catalog?: unknown;
  /** Los de tipo `bandeja` tienen `steps` y los corre test/stores.test.ts. */
  cases?: Array<Record<string, unknown>>;
}

function load(): Vector[] {
  return (readdirSync(VECTORS, { recursive: true }) as string[])
    .filter((file) => file.endsWith('.json'))
    .sort()
    .map((file) => JSON.parse(readFileSync(join(VECTORS, file), 'utf8')) as Vector);
}

const vectors = load();

function caught(run: () => unknown): unknown {
  try {
    run();
  } catch (error) {
    return error;
  }
  return undefined;
}
const label = (index: number, test: Record<string, unknown>): string =>
  `[${index}] ${typeof test['note'] === 'string' ? test['note'] : ''}`.trim();

it('hay vectores y son de la versión del spec que implementa el paquete', () => {
  expect(vectors.length, `no se encontraron vectores en ${relative(process.cwd(), VECTORS)}`).toBeGreaterThan(0);
  expect(readFileSync(join(SPEC, 'VERSION'), 'utf8').trim()).toBe(SPEC_VERSION);
  for (const vector of vectors) expect(vector.spec).toBe(SPEC_VERSION);
});

it('todos los tipos de vector tienen quien los corra', () => {
  const kinds = new Set(vectors.map((vector) => vector.kind));
  expect([...kinds].sort()).toEqual([
    'aviso',
    'bandeja',
    'canales',
    'catalogo',
    'matriz',
    'plantilla',
    'politica',
    'preferencias',
  ]);
});

for (const vector of vectors) {
  if (vector.kind === 'bandeja') continue;
  describe(vector.id, () => {
    (vector.cases ?? []).forEach((test, index) => {
      it(label(index, test), () => {
        switch (vector.kind) {
          case 'politica':
            if (test['valid']) {
              expect(() => parsePolicy(test['policy'])).not.toThrow();
            } else {
              expect(() => parsePolicy(test['policy'])).toThrow(InvalidPolicyError);
            }
            return;

          case 'canales':
            expect(
              resolveChannels(
                parsePolicy(vector.policy),
                test['category'] as string,
                test['overrides'] as Overrides,
                test['available'] as string[],
              ),
            ).toEqual(test['expect']);
            return;

          case 'matriz':
            expect(
              effectiveMatrix(
                parsePolicy(vector.policy),
                test['overrides'] as Overrides,
                test['available'] as string[],
              ),
            ).toEqual(test['expect']);
            return;

          case 'preferencias': {
            const run = (): unknown =>
              validatePatch(
                parsePolicy(vector.policy),
                test['patch'] as Record<string, Record<string, unknown>>,
                test['available'] as string[],
              );
            const expected = test['expect'] as
              | { ok: true; changes: unknown[] }
              | { ok: false; error: { category: string; channel: string | null; reason: string } };
            if (expected.ok) {
              expect(run()).toEqual(expected.changes);
              return;
            }
            let thrown: unknown;
            try {
              run();
            } catch (error) {
              thrown = error;
            }
            expect(thrown).toBeInstanceOf(PatchRejectedError);
            const rejected = thrown as PatchRejectedError;
            expect({
              category: rejected.category,
              channel: rejected.channel,
              reason: rejected.reason,
            }).toEqual(expected.error);
            return;
          }

          case 'plantilla': {
            const run = (): string =>
              renderTemplate(test['template'] as string, test['payload'] as Record<string, unknown>);
            const expected = test['expect'] as
              | { ok: true; text: string }
              | { ok: false; error: { reason: string; detail: string | null } };
            if (expected.ok) {
              expect(run()).toBe(expected.text);
              return;
            }
            const thrown = caught(run);
            const got =
              thrown instanceof TemplateError
                ? { reason: thrown.reason, detail: thrown.variable }
                : thrown instanceof InvalidTemplateError
                  ? { reason: 'invalid_template', detail: null }
                  : thrown;
            expect(got).toEqual(expected.error);
            return;
          }

          case 'catalogo': {
            const policy = parsePolicy(vector.policy);
            if (test['valid']) {
              expect(() => parseCatalog(test['catalog'], policy)).not.toThrow();
            } else {
              expect(() => parseCatalog(test['catalog'], policy)).toThrow(InvalidCatalogError);
            }
            return;
          }

          case 'aviso': {
            const catalog = parseCatalog(vector.catalog, parsePolicy(vector.policy));
            const run = (): unknown => {
              const draft = buildNotification(catalog, test['event'] as Record<string, unknown>);
              // Los vectores usan los nombres que viajan; el paquete, los de TypeScript.
              return {
                recipient_id: draft.recipientId,
                type: draft.type,
                category: draft.category,
                title: draft.title,
                body: draft.body,
                action_url: draft.actionUrl,
                payload: draft.payload,
                scope: draft.scope,
                dedupe_key: draft.dedupeKey,
              };
            };
            const expected = test['expect'] as
              | { ok: true; draft: unknown }
              | { ok: false; error: { reason: string; detail: string | null } };
            if (expected.ok) {
              expect(run()).toEqual(expected.draft);
              return;
            }
            const thrown = caught(run);
            expect(thrown).toBeInstanceOf(EventRejectedError);
            const rejected = thrown as EventRejectedError;
            expect({ reason: rejected.reason, detail: rejected.detail }).toEqual(expected.error);
            return;
          }

          case 'bandeja':
            throw new Error('los vectores de bandeja no tienen casos');
        }
      });
    });
  });
}
