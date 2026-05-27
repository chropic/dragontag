# Session handoff — branch `task/10-11-theme-wizard`

## What this branch is doing

Implementing **Tasks 10 & 11** from TASKS.md:

- **Task 10** — First-run setup wizard (`/setup` GET/POST routes, credential-fallback config reads)
- **Task 11** — terminal24 theme applied to all UI templates

## Backend: COMPLETE

All Python changes are done and uncommitted (modified in working tree):

| File | What changed |
|---|---|
| `dragontag/app/config.py` | `resolve_password()` + `resolve_acoustid_key()` now fall back to `config_path/password.hash` and `config_path/acoustid.key` (files the wizard writes) |
| `dragontag/app/main.py` | `require_auth` redirects to `/setup` when no password configured; `login_page`/`login_submit` likewise; new `/setup` GET (passes `username=env().username`) and POST (validates, writes hash, optionally writes acoustid key, redirects to `/login`) |

## Templates: PARTIALLY DONE

### ✅ Done (working tree, not committed)

| Template | Notes |
|---|---|
| `base.html` | Full terminal24 layout — IBM Plex Mono/Sans, CRT scanlines, black bg, `#0c0c0c` nav, zero border-radius |
| `login.html` | Standalone page (no base extend); terminal24 card |
| `setup.html` | **NEW FILE** (untracked); first-run wizard — credentials, AcoustID key, volumes info |
| `dashboard.html` | terminal24 cards + white upload button |
| `job_detail.html` | terminal24 dl/pre blocks |

### ❌ Still needs terminal24 styling (still have old slate-* classes)

| Template | Key things to fix |
|---|---|
| `_jobs_table.html` | `text-slate-400`, `border-slate-700/800`, `bg-emerald-700/red-700/amber-700/slate-700` status badges, `text-indigo-400` links |
| `review.html` | `bg-slate-800`, `text-indigo-400`, `bg-amber-700/red-700/indigo-700/emerald-700/slate-700` buttons, `bg-slate-900` inputs |
| `settings.html` | Most heavily themed — checkboxes, selects, many `bg-slate-*`/`text-slate-*` classes |
| `library.html` | Folder tabs, `bg-slate-*` section, search input |
| `_library_tracks.html` | Track table with `text-slate-*` classes |
| `library_folders.html` | Folder list + add form |

## Color mapping to apply (slate → terminal24)

| Old | New |
|---|---|
| `bg-slate-900` | `bg-black` (inputs/page bg) or `bg-[#0c0c0c]` (cards) |
| `bg-slate-800` | `bg-[#0c0c0c]` |
| `bg-slate-700` | `bg-[#131313]`; `hover:bg-slate-700` → `hover:bg-[#131313]` |
| `border-slate-700` / `border-slate-800` | `border-[#4a4a4a]` |
| `text-slate-100` | `text-white` |
| `text-slate-300` | `text-[#cfcfcf]` |
| `text-slate-400` / `text-slate-500` | `text-[#8a8a8a]` |
| `text-indigo-400` / `hover:text-indigo-300` | `text-[#cfcfcf]` / `hover:text-white` |
| `bg-indigo-600/700` buttons | `bg-white text-black`; hover → `hover:bg-[#cfcfcf]` |
| `bg-emerald-700` (done badge) | `bg-[#c7f0c7] text-black` |
| `bg-red-700` (error badge/button) | `bg-[#ffb4b4] text-black` |
| `bg-red-900/30` (error bg) | `bg-[#ffb4b4]/10` |
| `bg-amber-700` (needs_review badge) | `bg-[#f2e5b5] text-black` |
| `bg-amber-900/30` | `bg-[#f2e5b5]/10` |
| `rounded` / `rounded-lg` | remove (Tailwind config globally sets borderRadius to 0) |
| section headings with `font-semibold` | add `font-mono` too → `font-mono font-semibold` |

## To finish and ship

1. Apply terminal24 styling to the 6 remaining templates listed above
2. Commit everything on branch `task/10-11-theme-wizard`:
   ```
   feat: tasks 10/11 — first-run setup wizard + terminal24 theme
   ```
3. `gh pr create` to main (PR #7)
4. After PR merges, append a CHANGELOG entry (tasks 10 & 11)

## Reference: how setup.html works

- Standalone page (not extending `base.html`), same boilerplate as `login.html`
- Shows username as read-only display (from `{{ username }}` template var)
- Password + confirm password fields; AcoustID key (optional); Volumes info table
- POST to `/setup` → validates → writes `config_path/password.hash` (argon2) → optionally writes `config_path/acoustid.key` → redirect `/login`
- `/setup` is blocked once a password exists (both GET and POST redirect to `/login`)

## Reference: TASKS.md task numbers

Task 10 = "First-run setup wizard", Task 11 = "terminal24 theme". Both are under the "Onboarding & UI" section.
