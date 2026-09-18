# EverTree 3D 🌳

A cooperative 3D tree "Tamagotchi": Streamlit + Three.js (WebGL) + SQLAlchemy
(PostgreSQL/SQLite), with a Telegram bot for Telegram Stars monetization.

**Single-file deploy**: `app.py` runs the Streamlit UI *and* the Telegram bot
together (the bot runs in a background thread of the same process). You only
run one command, on one free Streamlit Cloud app — no separate bot server.

## Features

- **Username/password login** with auto-registration — no Telegram account
  needed to play. New username + password = new account, existing username
  needs the matching password.
- **Invite codes**: every Grove has a short code (shown on the Team tab).
  Enter it at login (or on the "Join with a code" tab) to join a teammate's
  Grove — no Telegram deep links involved.
- **Coop teams (1-5 players)**: dynamic role assignment across 5 roles —
  Suvchi (Waterer), Bog'bon (Gardener), Quyoshchi (Sunlight), Tozalovchi
  (Cleaner, interactive 3D pest-clearing), Parvarishchi (Nurturer).
- **6h/7h cycle, both directions**: tasks reset every 6h; a 1h warning phase
  follows. If *every* role hits its target for the cycle, the tree **levels
  up**. If *any* role misses it (and no shield is active), the tree **levels
  down** (minimum level 1). Difficulty scales — level N requires N
  completions per task per cycle.
- **Telegram Stars "Magical Shield"**: 50 ⭐ for 10 days of degradation
  immunity, purchased via the bot (`/shield <invite code>`), rendered as a
  glowing golden force-field around the tree.
- **Realistic 3D broadleaf tree**: recursive branching, instanced leaf
  canopy, gradient sky, grass, sunlight/shadows, orbit camera, growth stages
  from a buried seed up through a golden/pink canopy, wildlife, and a
  fairytale cottage at Level 31+.

## Project layout

```
app.py                     Everything: Streamlit UI, auth, game logic wiring,
                            AND the embedded Telegram bot (bottom of the file)
database.py                SQLAlchemy models + all game logic (auth, roles, timers, shield)
components/three_tree.py   Three.js HTML component (3D tree, pest raycaster)
requirements.txt
.env.example
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in BOT_TOKEN, WEBAPP_URL, DATABASE_URL, etc.
export $(grep -v '^#' .env | xargs)
```

### Run it (one command, does both)

```bash
streamlit run app.py
```

That's it — if `BOT_TOKEN` is set, the Telegram bot starts automatically in
a background thread the first time the app runs. Check the sidebar: it shows
"🤖 Telegram bot: running" once it's up.

Deploy this same command on Streamlit Community Cloud. Put your `BOT_TOKEN`,
`BOT_USERNAME`, `WEBAPP_URL`, and (optionally) `DATABASE_URL` in the app's
**Settings → Secrets** as a `[secrets]`-style TOML, or however your platform
exposes environment variables — `os.environ.get(...)` picks them up the same
way either way.

**Streamlit Cloud free-tier caveat**: a free-tier app sleeps after a period
of no visitors, and the bot only runs while the app is awake. That means the
bot may occasionally be offline for a bit — visiting the app wakes it back
up. This is a real limitation of running a bot piggybacked on a free web
app; a dedicated always-on host (see below) doesn't have this problem.

### If you outgrow the free tier

The bot's code lives entirely inside `app.py` (see `run_bot_polling()` near
the top of the file) for convenience, but nothing stops you from splitting
it back out into its own always-on process later — a small VPS with
`systemd`, Railway, Render, etc. — if you want the bot online even while the
web app is asleep. The function is self-contained and only needs `BOT_TOKEN`
and access to `database.py`.

### Database

Defaults to local SQLite (`evertree3d.db`) if `DATABASE_URL` is unset. Note
that **schema changed** in this version (added `username`/`password_hash` to
`User`, renamed a `TreeState` column) — delete any old `evertree3d.db` from
a previous version before running, since `init_db()` only creates missing
tables, it doesn't migrate existing ones. On Streamlit Community Cloud the
filesystem is ephemeral anyway, so this is a non-issue there. For anything
you want to persist across redeploys, point `DATABASE_URL` at Postgres:

```
postgresql+psycopg2://user:password@host:5432/evertree3d
```

## How the pieces fit together

- **Login**: `register_or_login(db, username, password)` in `database.py`
  looks the username up; if it exists it verifies the password (PBKDF2-HMAC,
  salted), otherwise it auto-registers. The invite-code field on the same
  form calls `join_team_by_code` right after a successful login/registration.
- **Cycle outcome**: `evaluate_cycle(db, team)` runs on every page load. It
  looks at the *previous* 6h cycle (once its 7h deadline has passed): if
  every role hit its required completions, `tree.level += 1`; if any role
  missed it and no shield is active, `tree.level -= 1` (floor 1); if a role
  missed it *with* a shield active, nothing changes. A `last_cycle_evaluated`
  timestamp on `TreeState` guards against double-processing the same cycle,
  and cycles that started before the tree existed are skipped rather than
  counted as a miss.
- **The 3D tree** (`components/three_tree.py`) is a self-contained HTML/JS
  payload rendered via `streamlit.components.v1.html`. It builds an organic
  branching structure recursively, scatters an instanced "leaf clump" canopy
  around the branch tips, and layers in a gradient sky, grass, and sunlight.
  When `pest_mode=True` it spawns clickable pest meshes with a raycaster;
  clearing them all posts a `pests_cleared` message back to the parent window.
- **The Telegram bot** (bottom of `app.py`, `run_bot_polling()`) is started
  once via `st.cache_resource` in a daemon background thread. It only
  handles the Telegram Stars purchase flow now — `/shield <invite code>`
  sends an invoice, and the `successful_payment` handler calls
  `activate_shield(...)`. Since accounts are no longer tied to Telegram IDs,
  the bot looks teams up by invite code instead of by user.

## Notes / production hardening ideas

- Passwords are hashed with PBKDF2-HMAC-SHA256 (100k iterations, random salt
  per user) using only the standard library — no extra dependency, but for
  a larger deployment consider `argon2` or `bcrypt` instead.
- Add a scheduled sweep (`JobQueue`, or cron + a `evaluate_cycle` pass over
  all teams) so growth/degradation is evaluated even when no one opens the
  app — currently it's lazily checked on page load.
- Rate-limit `complete_task` per role/user if you want to prevent one player
  from spamming a task alone to hit the scaling requirement.
- **Rotate any bot token that has ever been committed to a file you shared**
  — treat it like a password. Generate a fresh one via @BotFather → your bot
  → `/revoke`.
