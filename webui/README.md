# AndroRemote web console

Operator console for the C2 server: sessions, screen, control, data, files,
terminal, cache and settings. React 19 + Vite + TypeScript + Tailwind v4 with
shadcn/ui components.

## Development

The C2 server serves the built console from `androremote/web/dist`. For
live-reload development, run the Vite dev server and point it at your C2 web UI:

```sh
npm install
npm run dev                      # http://localhost:5173
```

`vite.config.ts` proxies `/api` to `http://127.0.0.1:8888`, the default
`--web-port`. Point it elsewhere if you started the server on another port or
host:

```sh
VITE_API_TARGET=http://10.0.0.5:9000 npm run dev
```

Start the server with the web UI enabled in another shell:

```sh
python3 androremote.py --web --web-port 8888
```

Without a reachable backend the console renders an explicit "API backend is not
reachable" state with a retry, rather than empty screens.

## Build

`npm run build` type-checks and writes straight into `androremote/web/dist`,
which is what the Python server serves (that directory is gitignored). Run it
before starting the server if you changed anything under `src/`.

## Lint

```sh
npm run lint
```

## Preferences

Theme, snapshot refresh interval and rows per page are per-browser settings,
edited in the console's Settings view and stored in `localStorage` under
`arprefs`. Tunnel and listener configuration stays on the server command line
(`c2.py --setup-tunnel`, `--web-port`, and the rest); the console only reports
it.
