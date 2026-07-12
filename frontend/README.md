# Insyte Studio — frontend

Insyte Studio's UI is served by the FastAPI backend from
[`src/insyte/studio_dist/`](../src/insyte/studio_dist). That directory is what ships **inside
the Python wheel**, so installed users need **no Node.js** — `pip install insyte && insyte
studio` just works.

## What ships today

`studio_dist/` currently contains a **self-contained, build-free SPA** (`index.html` +
`assets/app.css` + `assets/app.js`, no external dependencies, CSP-safe and offline). It
implements the workspace shell (header, collapsible sidebar, status bar, light/dark themes),
the analytics chat with **SSE streaming**, result cards (Overview / Chart / Data / SQL /
Method / Warnings) with inline SVG charts, and the Schema / Metrics / History / Settings
pages. It also includes the saved-investigations workspace (`#/investigations` and
`#/investigations/<id>`), report reading modes (Executive / Analyst / Data Quality / Actions),
and client-side Markdown/JSON exports for reports and investigation bundles. It talks only to
the documented `/api` endpoints.

## Developing the React version

This directory is the Vite + React + TypeScript project intended to grow into the richer UI
(TanStack Query/Table, Zustand, Recharts/ECharts, shadcn/ui, Tailwind). Building it **replaces**
the build-free SPA in `studio_dist/`.

```bash
cd frontend
npm install
npm run build      # outputs to ../src/insyte/studio_dist (index.html + assets/)
```

`npm run dev` runs Vite's dev server and proxies `/api` to a running `insyte studio` backend.

### Contract

The frontend depends only on the JSON/SSE contract in
[`src/insyte/studio/schemas.py`](../src/insyte/studio/schemas.py) and the endpoints in
`src/insyte/studio/routes/`. It must never receive database credentials, and every analytical
request is streamed from `POST /api/conversations/{id}/messages` → SSE
`GET /api/analyses/{id}/events`.

Saved investigations are read from:

- `GET /api/investigations`
- `GET /api/investigations/{id}`

The frontend should treat the saved investigation `result` as a normal `AnalysisResult` and
reuse the same rendering path as live chat results.
