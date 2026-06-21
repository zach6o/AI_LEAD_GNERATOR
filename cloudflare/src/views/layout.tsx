import { html } from "hono/html";
import type { FC, PropsWithChildren } from "hono/jsx";

export const Layout: FC<PropsWithChildren<{ title?: string }>> = ({ title, children }) => (
  <>
    {html`<!doctype html>`}
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <title>{title ?? "Lead Generator"}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <script src="https://cdn.tailwindcss.com"></script>
        <style>{`
          body { background: linear-gradient(180deg,#f8fafc 0%,#eef2f7 100%); min-height:100vh; }
          .card { background:white; border:1px solid #e2e8f0; border-radius:12px; box-shadow:0 1px 2px rgba(15,23,42,.04); }
          .badge { display:inline-flex; align-items:center; padding:2px 8px; font-size:11px; font-weight:600; border-radius:9999px; text-transform:uppercase; letter-spacing:.04em; }
          .table-row:hover { background:#f8fafc; }
        `}</style>
      </head>
      <body class="text-slate-800">
        <header class="border-b border-slate-200 bg-white/80 backdrop-blur sticky top-0 z-10">
          <div class="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
            <a href="/" class="flex items-center gap-2 text-lg font-semibold tracking-tight">
              <span class="inline-block w-2 h-2 rounded-full bg-emerald-500"></span>
              Lead Generator
              <span class="text-slate-400 font-normal text-sm">/ CRM</span>
            </a>
            <nav class="flex items-center gap-4 text-sm">
              <a href="/" class="text-slate-500 hover:text-slate-800">Dashboard</a>
              <a href="/approvals" class="text-slate-500 hover:text-slate-800">Approvals</a>
            </nav>
          </div>
        </header>
        <main class="max-w-7xl mx-auto px-6 py-6">{children}</main>
      </body>
    </html>
  </>
);
