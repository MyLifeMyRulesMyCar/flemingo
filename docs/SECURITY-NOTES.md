# Security notes — accepted risk log

Dependency advisories that `npm audit` / `pip-audit` flag but that this
project has deliberately not upgraded past, with the reasoning, so nobody
has to re-derive it later or assumes it was simply missed.

## dashboard: react-router-dom — GHSA-wrjc-x8rr-h8h6 / GHSA-337j-9hxr-rhxg

- **Flagged:** `npm audit --omit=dev` in `dashboard/` (2026-08-02).
- **Actual advisories (verified from `npm audit` output, not assumed):**
  - **GHSA-wrjc-x8rr-h8h6** — open redirect via backslash in `<Link>`
    and `useNavigate` (CVE-2025-68470 bypass). Affects `react-router`
    6.0.0–7.17.0.
  - **GHSA-337j-9hxr-rhxg** — arbitrary constructor injection via
    `deserializeErrors()` in SSR hydration. Same affected range.
- **Status:** `react-router-dom` is pinned to `^6.28.0`, resolved to
  6.30.4. No 6.x fix exists for either advisory — the only fixed
  version is 7.13.0+ (`react-router` 7.x line). `npm audit fix` does
  nothing (stays within the `^6.28.0` range; `--force` would attempt a
  breaking vite upgrade, not a react-router version bump).
- **Why we haven't upgraded to 7.x:** a 6→7 major migration brings real
  risk (data-router idioms, removed APIs) for zero actual exposure
  reduction — neither advisory's vulnerable code path is reachable in
  this application (see below).
- **Reachability — GHSA-wrjc-x8rr-h8h6 (open redirect):** the vulnerable
  API surface (`<Link>`, `useNavigate`) **is** present in the
  codebase (`<NavLink>` in `dashboard/src/components/Sidebar.jsx`,
  `<Navigate>` in `dashboard/src/App.jsx`). However, **every `to` prop
  is a hardcoded static string** — `NAV_ITEMS` in Sidebar.jsx defines
  `/`, `/io`, `/can`, `/modbus`, `/mqtt`, `/system`, `/modbus-tcp`;
  App.jsx uses `/login` and `/`. No user-controlled input flows into
  any navigation target. The vulnerability requires attacker-controlled
  path injection (a backslash), which is impossible with static routes.
- **Reachability — GHSA-337j-9hxr-rhxg (SSR injection):** requires
  `deserializeErrors()`, which is a server-side rendering API only.
  This dashboard is a client-side SPA (Vite build, `BrowserRouter`),
  no SSR. Unreachable.
- **What would change this:** (a) a 6.x backport ships — trivial
  `npm update react-router-dom`, do it then; or (b) any new code
  introduces user-controlled input into a `<Link>`/`<Navigate>` `to`
  prop, or adds SSR — re-run the reachability check above before
  merging, and upgrade to 7.13.0+.
- **CI:** `npm audit --omit=dev` runs on every build (advisory, does
  not fail CI — see `.github/workflows/ci.yml`) so this doesn't
  silently disappear from view.
