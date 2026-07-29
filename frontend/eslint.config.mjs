import js from "@eslint/js";
import tseslint from "typescript-eslint";
import pluginVue from "eslint-plugin-vue";
import eslintConfigPrettier from "eslint-config-prettier";
import eslintPluginPrettier from "eslint-plugin-prettier/recommended";

export default tseslint.config(
  // 全局忽略
  { ignores: ["dist/", "node_modules/", "*.config.*"] },

  // 基础 JS 推荐规则
  js.configs.recommended,

  // TypeScript 推荐规则
  ...tseslint.configs.recommended,

  // Vue 推荐规则
  ...pluginVue.configs["flat/essential"],
  {
    files: ["*.vue", "**/*.vue"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },

  // 浏览器环境声明
  {
    files: ["src/**/*.{ts,vue}"],
    languageOptions: {
      globals: {
        window: "readonly",
        document: "readonly",
        HTMLElement: "readonly",
        HTMLInputElement: "readonly",
        MouseEvent: "readonly",
        KeyboardEvent: "readonly",
        Event: "readonly",
        DragEvent: "readonly",
        Node: "readonly",
        confirm: "readonly",
        fetch: "readonly",
        AbortController: "readonly",
        TextDecoder: "readonly",
        ReadableStream: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        requestAnimationFrame: "readonly",
        IntersectionObserver: "readonly",
        MutationObserver: "readonly",
        ResizeObserver: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        Blob: "readonly",
        File: "readonly",
        FormData: "readonly",
        navigator: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        history: "readonly",
        location: "readonly",
      },
    },
  },

  // TypeScript + Vue 自定义规则
  {
    files: ["src/**/*.ts", "src/**/*.vue"],
    rules: {
      "vue/multi-word-component-names": "off",
      "no-console": "warn",
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_" },
      ],
    },
  },

  // Prettier（必须放在最后，覆盖所有格式规则）
  eslintConfigPrettier,
  eslintPluginPrettier,
);
