import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

/**
 * Lints the web app and the shared packages it builds from. Globs are
 * relative to the repo root: `npm run lint` runs
 * `eslint -c apps/web/eslint.config.js apps/web packages` from there
 * (with -c, ESLint's base path is the working directory).
 */
export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/test-results/**',
      '**/playwright-report/**',
      'apps/api/**',
      'apps/bridge/**',
      'docs/**',
      'db/**',
      'deploy/**',
      'installer/**',
      'plugins/**',
    ],
  },
  {
    files: ['apps/web/**/*.{ts,tsx}', 'packages/*/src/**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['apps/web/*.config.{ts,js}', 'apps/web/e2e/**/*.ts', 'apps/web/test/**/*.ts', 'packages/tokens/scripts/**/*.mjs'],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    files: ['packages/tokens/scripts/**/*.mjs', 'apps/web/eslint.config.js'],
    extends: [js.configs.recommended],
    languageOptions: { sourceType: 'module', globals: { ...globals.node } },
  },
);
