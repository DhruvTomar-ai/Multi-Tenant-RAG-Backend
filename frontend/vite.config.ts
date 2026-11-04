import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";

// The backend has no CORS settings, so the browser only ever talks to this origin and /api is forwarded.
const proxyFor = (target: string) => ({ "/api": { target, changeOrigin: true } });

// Added to production builds only, because Vite's development server relies on inline scripts.
const CONTENT_SECURITY_POLICY = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'";

const contentSecurityPolicy = (): Plugin => ({
  name: "content-security-policy",
  apply: "build",
  transformIndexHtml: (html) => html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CONTENT_SECURITY_POLICY}" />`),
});

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.API_PROXY_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [react(), tailwindcss(), contentSecurityPolicy()],
    server: { port: 5173, proxy: proxyFor(target) },
    preview: { port: 4173, proxy: proxyFor(target) },
  };
});
