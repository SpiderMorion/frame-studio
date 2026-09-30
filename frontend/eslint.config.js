import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default [
  { ignores: ['dist/**', 'node_modules/**', '*.tsbuildinfo'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ['**/*.{js,ts,tsx}'], languageOptions: { globals: { ...globals.browser, ...globals.node } } },
];
