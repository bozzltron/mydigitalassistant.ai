import js from '@eslint/js'
import solid from 'eslint-plugin-solid/configs/recommended'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  solid,
  {
    languageOptions: {
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
        project: './tsconfig.json',
      },
    },
    rules: {
      'solid/jsx-no-undef': 'off',
    },
  },
)