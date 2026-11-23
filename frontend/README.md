# ACIO web interface (React + Vite + TypeScript)

The prebuilt interface is in `dist/` and is served by the FastAPI back end (`python -m app.server` from the repo root),
so running the app needs Python only. Node.js is needed only to change the interface.

    npm install
    npm run dev      # http://localhost:5173, forwards /api, /files and /audit to the back end on port 8765
    npm run build    # rebuilds dist/; commit it so users without Node get the change

Source: `src/App.tsx` (tabs, chat, clarifying questions), `RunPanel.tsx` (live run), `DecisionView.tsx` (decision,
allocation, lineup, Red Team, memo), `RunsPage.tsx` (all runs and memos), `cast.tsx` (agent cards drawn in SVG),
`labels.ts` (decision labels), `markdown.tsx` (safe memo renderer), `api.ts` (typed API client).
