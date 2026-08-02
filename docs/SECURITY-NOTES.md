# Security notes — accepted risk log

Dependency advisories that `npm audit` / `pip-audit` flag but that this
project has deliberately not upgraded past, with the reasoning, so nobody
has to re-derive it later or assumes it was simply missed.

## dashboard: react-router-dom — 3 advisories, all currently unaddressed

`npm audit --omit=dev` (2026-08-02) reports **three** distinct GHSA IDs
against the locked version (`react-router-dom` pinned `^6.28.0`, resolved
6.30.4) — not two. Verified from `npm audit --omit=dev --json`, not from
the plain-text summary, since the summary groups them under one
`react-router` entry and it's easy to read that as "two advisories" and
miss the third, narrower one that's specific to `react-router-dom` itself.

- **GHSA-wrjc-x8rr-h8h6** — open redirect via backslash in `<Link>` and
  `useNavigate` (CVE-2025-68470 bypass). Range `>=6.0.0 <7.18.0`.
- **GHSA-337j-9hxr-rhxg** — arbitrary constructor injection via
  `deserializeErrors()` in SSR hydration. Range `>=6.4.0 <7.18.0`.
- **GHSA-jjmj-jmhj-qwj2** — open redirect leading to XSS. Range
  `>=6.30.2 <=6.30.4` — specific to `react-router-dom`, narrower than the
  other two, and the one actually bounding the currently-locked version.

**Status:** no 6.x release fixes any of the three. All three are fixed
by upgrading to `react-router-dom` 7.18.0+.

**Why we haven't upgraded to 7.x:** tried it (`npm install
react-router-dom@latest`, landed on 7.18.2). It clears all three of the
above — but `npm audit --omit=dev` then reports a **high**-severity
advisory instead: **GHSA-qwww-vcr4-c8h2**, "RSC Mode CSRF Bypass Allows
Action Execution Before 400 Response," range `7.12.0–8.2.0`. npm's own
suggested remediation for that one is `npm audit fix --force`, which
downgrades to 7.11.0 — which is back inside the `<7.18.0` range for the
first two advisories above. **There is currently no published version of
react-router-dom that clears every open advisory simultaneously.**
A 6→7 migration also brings real risk (data-router idioms, some removed
APIs) for an app that doesn't need any of what 7.x adds.

**Reachability — all three moderate advisories (open redirect /
SSR injection):** checked every navigation-target call site in
`dashboard/src`:
`grep -rn "useNavigate\|navigate(\|<Link\|<NavLink\|<Navigate" dashboard/src/`
finds exactly 4 uses: `<NavLink>` in `Sidebar.jsx` (`to={item.to}`,
where `item` comes from `NAV_ITEMS`, a module-level array of **7
hardcoded string literals** — `/`, `/io`, `/can`, `/modbus`, `/mqtt`,
`/system`, `/modbus-tcp`) and three `<Navigate to="...">` calls in
`App.jsx`, all with literal string targets (`/login`, `/`, `/`). No
`useNavigate()` calls anywhere. No user-controlled input reaches any
navigation target — the backslash-injection and SSR-hydration
vulnerable paths both require dynamic/attacker-influenced `to` values or
an SSR entrypoint, neither of which exists here (this is a Vite CSR SPA,
`BrowserRouter`, no server rendering).

**Reachability — GHSA-qwww-vcr4-c8h2 (RSC, only relevant if we ever
upgrade to 7.x):** requires React Server Components mode. No RSC APIs
anywhere in this codebase (`grep -rn "react-server\|\"use server\"\|unstable_RSC" dashboard/src/`
— no matches), and Vite doesn't do RSC without dedicated setup this
project doesn't have. Also unreachable, noted here so it doesn't need
re-discovering if a future 7.x upgrade is considered.

**What would change this:** (a) a 6.x backport ships for the three
moderate advisories — trivial `npm update react-router-dom`, do it
then; or (b) any new code introduces user-controlled input into a
`<Link>`/`<NavLink>`/`<Navigate>` `to` prop, calls `useNavigate()` with
a dynamic target, adds SSR, or adopts RSC — re-run the relevant
reachability grep above before merging, and re-evaluate.

**CI:** `npm audit --omit=dev` runs on every build (advisory, does not
fail CI — see `.github/workflows/ci.yml`) so none of this silently
disappears from view.
