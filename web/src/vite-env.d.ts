/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API 基础路径，默认 /api（见 web/.env.example） */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
