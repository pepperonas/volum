// @ts-check
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

/**
 * Type-aware linting, which is the only kind worth the run time here: the
 * rules that matter in this code are about promises that are never awaited and
 * effects with the wrong dependencies, and neither is visible without types.
 */
export default tseslint.config(
  {
    ignores: ["dist/**", "src-tauri/target/**", "src-tauri/gen/**", ".playwright-mcp/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // `void promise` is how this code says "started on purpose, not awaited";
      // the rule is kept for everything that is not marked that way.
      "@typescript-eslint/no-floating-promises": ["error", { ignoreVoid: true }],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
