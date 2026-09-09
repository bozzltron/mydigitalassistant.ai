import tjs from '@eslint/js';
import solid from 'eslint-plugin-solid/configs/recommended';

export default [
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
    },
    rules: {
      eqeqeq: "error",
      "no-unused-vars": "error",
      "no-undef": "error",
      semi: ["error", "always"],
      quotes: ["error", "double"],
      indent: ["error", 2],
    },
  },
  {
    languageOptions: {
      env: {
        browser: true,
        es2021: true,
      },
    },
  },
  solid,
];
