import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every `import './x.css'` resolves to a file that exists.
 *
 * `OverflowMenu.tsx` imported a stylesheet that was never written, and nothing
 * caught it: `tsc` does not resolve a `.css` import, vitest stubs one, and the
 * organization gate reads TypeScript. It surfaced only when a throwaway
 * consumer ran a real bundler and failed on its first build.
 *
 * A consumer's bundler is the first thing that resolves these, which makes a
 * missing one a defect nobody here can see and every consumer hits immediately.
 */

const SRC = resolve(__dirname, '..', 'src');

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(entry) ? [full] : [];
  });
}

const RELATIVE_IMPORT = /from\s+['"](\.[^'"]+)['"]|import\s+['"](\.[^'"]+)['"]/g;

describe('every relative import resolves', () => {
  const files = sourceFiles(SRC);

  it('finds the source tree', () => {
    expect(files.length).toBeGreaterThan(30);
  });

  it('resolves every imported stylesheet', () => {
    const missing: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(RELATIVE_IMPORT)) {
        const target = match[1] ?? match[2];
        if (!target.endsWith('.css')) continue;
        if (!existsSync(resolve(dirname(file), target))) {
          missing.push(`${file.slice(SRC.length + 1)} -> ${target}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it('resolves every imported module', () => {
    const missing: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(RELATIVE_IMPORT)) {
        const target = match[1] ?? match[2];
        if (target.endsWith('.css')) continue;
        const base = resolve(dirname(file), target);
        const found = ['.ts', '.tsx', '/index.ts', '/index.tsx', ''].some((suffix) =>
          existsSync(base + suffix),
        );
        if (!found) missing.push(`${file.slice(SRC.length + 1)} -> ${target}`);
      }
    }
    expect(missing).toEqual([]);
  });
});
