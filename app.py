"""
app.py
EverTree 3D — single-file deploy: the Streamlit Mini App AND the Telegram
bot both run from this one file, so `streamlit run app.py` is all you need
(no separate bot process/server — the bot runs in a background thread of
the same process, started once via st.cache_resource).

Handles:
    - Username/password login screen with auto-registration (no Telegram
      account required to use the app) + an invite-code field to join a team.
    - UI tabs: 3D Tree View, Team Status, Tasks, Shop/Shield, Leaderboard.
    - The Telegram bot (bottom of file): /start, /shield <code>, /status <code>,
      and the Telegram Stars payment flow for the Magical Shield — started
      automatically in the background if BOT_TOKEN is set.
"""

from __future__ import annotations

import logging
import os
import threading

import streamlit as st

from components.three_tree import render_tree
from database import (
    ALL_ROLES,
    ROLE_LABELS,
    SHIELD_COST_STARS,
    SHIELD_DURATION_DAYS,
    Role,
    TASK_CYCLE_HOURS,
    Team,
    TreeState,
    evaluate_cycle,
    complete_task,
    create_team,
    get_active_shield,
    get_session,
    has_active_shield,
    init_db,
    join_team_by_code,
    register_or_login,
    roles_for_user,
    task_status,
)

st.set_page_config(page_title="EverTree 3D", page_icon="🌳", layout="wide")
init_db()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("evertree3d")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "EverTree3DBot")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")


# =========================================================================== #
# Background Telegram bot (defined near the bottom of the file; see
# `run_bot_polling` and `_start_bot_background`). Started here, once.
# =========================================================================== #

# =========================================================================== #
# Telegram bot — merged into this file so it runs alongside the Streamlit
# app in a single process (see _start_bot_background() near the top).
# Only used for the Telegram Stars "Magical Shield" purchase flow; account
# login/registration happens entirely in the Streamlit UI above.
# =========================================================================== #

def run_bot_polling() -> None:
    """Blocking call — runs in its own background thread. Builds and starts
    the python-telegram-bot Application. `stop_signals=None` is required
    here because signal handlers can only be registered on the main thread,
    and this always runs on a background thread."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update, WebAppInfo
        from telegram.ext import (
            Application,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            PreCheckoutQueryHandler,
            filters,
        )
    except ImportError:
        logger.warning("python-telegram-bot not installed — bot will not start.")
        return

    from database import Team as _Team, activate_shield as _activate_shield

    def _webapp_keyboard() -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌳 Open EverTree 3D", web_app=WebAppInfo(url=WEBAPP_URL))]]
        )

    async def start_cmd(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        await update.message.reply_text(
            "🌳 Welcome to EverTree 3D!\n\n"
            "Grow a magical tree together with up to 5 friends. Log in with a username/password "
            "in the Mini App, then share your Grove's invite code with teammates.\n\n"
            "Use /shield <code> here any time to buy a Magical Shield with Telegram Stars.\n\n"
            "Tap below to open the Mini App:",
            reply_markup=_webapp_keyboard(),
        )

    async def send_shield_invoice(update: "Update", context: "ContextTypes.DEFAULT_TYPE", team_id: int) -> None:
        chat_id = update.effective_chat.id
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="10-Day Magical Shield",
            description="Protects your EverTree from degradation for 10 days, even if tasks are missed.",
            payload=f"shield:{team_id}",
            provider_token="",  # Telegram Stars payments use an empty provider_token
            currency="XTR",     # Telegram Stars currency code
            prices=[LabeledPrice("Magical Shield (10 days)", SHIELD_COST_STARS)],
        )

    async def shield_cmd(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage: /shield <invite code>\nFind your Grove's code on the Team tab in the Mini App."
            )
            return
        code = args[0].strip().upper()
        sdb = get_session()
        team = sdb.query(_Team).filter_by(referral_code=code).first()
        sdb.close()
        if not team:
            await update.message.reply_text("No Grove found with that invite code.")
            return
        await send_shield_invoice(update, context, team_id=team.id)

    async def status_cmd(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /status <invite code>")
            return
        code = args[0].strip().upper()
        sdb = get_session()
        team = sdb.query(_Team).filter_by(referral_code=code).first()
        if not team:
            await update.message.reply_text("No Grove found with that invite code.")
            sdb.close()
            return
        tree_ = team.tree
        lines = [f"🌳 *{team.name}* — Level {tree_.level} ({tree_.xp} XP)"]
        for role in ALL_ROLES:
            s = task_status(sdb, team, role)
            dot = {"done": "🟢", "warning": "🟡", "missed": "🔴", "pending": "⚪", "shielded": "🛡️"}[s["state"]]
            lines.append(f"{dot} {ROLE_LABELS[role]}: {s['completions']}/{s['required']}")
        sdb.close()
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def pre_checkout(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        query = update.pre_checkout_query
        if query.invoice_payload.startswith("shield:"):
            await query.answer(ok=True)
        else:
            await query.answer(ok=False, error_message="Unknown item.")

    async def successful_payment(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        payment = update.message.successful_payment
        payload = payment.invoice_payload  # "shield:<team_id>"
        if not payload.startswith("shield:"):
            return
        team_id = int(payload.split(":", 1)[1])
        sdb = get_session()
        team = sdb.query(_Team).filter_by(id=team_id).first()
        if team:
            shield = _activate_shield(
                sdb,
                team,
                purchaser_telegram_id=str(update.effective_user.id),
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                stars_paid=payment.total_amount,
            )
            await update.message.reply_text(
                f"🛡️ Magical Shield activated for {team.name}! "
                f"Active until {shield.expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
            )
        sdb.close()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("shield", shield_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    logger.info("EverTree 3D bot starting (polling, background thread)...")
    try:
        application.run_polling(stop_signals=None, drop_pending_updates=True, close_loop=False)
    except Exception:
        logger.exception("Telegram bot polling stopped with an error")


@st.cache_resource(show_spinner=False)
def _start_bot_background() -> bool:
    """
    Launches the Telegram bot's polling loop in a daemon background thread.
    @st.cache_resource makes this run exactly once per app process, no
    matter how many times Streamlit reruns this script or how many users
    open the app — it's a process-wide singleton, not per-session.
    """
    if not BOT_TOKEN:
        return False
    try:
        thread = threading.Thread(target=run_bot_polling, daemon=True, name="evertree-bot")
        thread.start()
        return True
    except Exception:
        logger.exception("Failed to start Telegram bot background thread")
        return False


bot_running = _start_bot_background()

# --------------------------------------------------------------------------- #
# Login / Registration
# --------------------------------------------------------------------------- #
if "user_id" not in st.session_state:
    st.title("🌳 EverTree 3D")
    st.subheader("Grow a magical tree together with your squad")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        invite_code = st.text_input(
            "Invite code (optional)",
            help="Have a code from a teammate? Enter it here to join their Grove.",
        )
        st.caption("New here? Just pick a username and password — an account is created automatically.")
        submitted = st.form_submit_button("Continue", type="primary")

    if submitted:
        db = get_session()
        user, error = register_or_login(db, username, password)
        if error:
            st.error(error)
            db.close()
        else:
            st.session_state.user_id = user.id
            if invite_code.strip() and not user.team_id:
                joined = join_team_by_code(db, user, invite_code.strip())
                if joined:
                    st.session_state["_just_joined"] = joined.name
                else:
                    st.session_state["_invite_failed"] = True
            db.close()
            st.rerun()
    st.stop()

# --------------------------------------------------------------------------- #
# Authenticated session
# --------------------------------------------------------------------------- #
db = get_session()
from database import User as UserModel  # local import to avoid polluting the top-level import list

user = db.query(UserModel).filter_by(id=st.session_state.user_id).first()
if user is None:
    # Session points at a user that no longer exists (e.g. DB was reset) — send back to login.
    del st.session_state["user_id"]
    db.close()
    st.rerun()

with st.sidebar:
    st.caption(f"Signed in as **{user.display_name}**")
    if st.button("Log out"):
        del st.session_state["user_id"]
        st.rerun()
    if bot_running:
        st.caption("🤖 Telegram bot: running")
    elif BOT_TOKEN:
        st.caption("🤖 Telegram bot: failed to start (check logs)")
    else:
        st.caption("🤖 Telegram bot: not configured (set BOT_TOKEN)")

if st.session_state.pop("_just_joined", None):
    st.toast(f"Joined {st.session_state.get('_just_joined', '')}! 🌳", icon="🎉")
if st.session_state.pop("_invite_failed", False):
    st.toast("That invite code is invalid or the team is full.", icon="⚠️")

# --------------------------------------------------------------------------- #
# No team yet -> onboarding (create a Grove, or join with a code)
# --------------------------------------------------------------------------- #
if not user.team_id:
    st.title("🌳 EverTree 3D")
    st.write(f"Welcome, **{user.display_name}**!")

    tab_new, tab_join = st.tabs(["🌱 Start a new Grove", "🔑 Join with a code"])
    with tab_new:
        name = st.text_input("Name your Grove", value=f"{user.display_name}'s Grove")
        if st.button("🌱 Start a new Grove", type="primary"):
            create_team(db, user, name=name)
            st.rerun()
    with tab_join:
        code = st.text_input("Invite code")
        if st.button("🔑 Join Team"):
            joined = join_team_by_code(db, user, code.strip())
            if joined:
                st.success(f"Joined {joined.name}!")
                st.rerun()
            else:
                st.error("That invite code is invalid or the team is full.")
    db.close()
    st.stop()

team = user.team
tree = team.tree

# --------------------------------------------------------------------------- #
# Cycle outcome check (grow on a clean cycle / degrade on a missed one) —
# runs on every page load.
# --------------------------------------------------------------------------- #
alert = evaluate_cycle(db, team)
if alert:
    if "grew" in alert or "🌱" in alert:
        st.success(alert)
    else:
        st.error(alert)

shield_active = has_active_shield(db, team)
active_shield = get_active_shield(db, team) if shield_active else None
my_roles = roles_for_user(user)

# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
header_l, header_r = st.columns([3, 1])
with header_l:
    st.markdown(f"## 🌳 {team.name}")
    cols = st.columns(4)
    cols[0].metric("Tree Level", tree.level)
    cols[1].metric("Team Size", f"{team.size}/5")
    cols[2].metric("XP", tree.xp)
    cols[3].metric("Shield", "🛡️ Active" if shield_active else "—")
with header_r:
    st.caption(f"**{user.display_name}**")
    if my_roles:
        st.caption(" / ".join(ROLE_LABELS[r] for r in my_roles))

st.divider()

tab_tree, tab_team, tab_tasks, tab_shop, tab_board = st.tabs(
    ["🌳 3D Tree", "👥 Team", "✅ Tasks", "🛒 Shop", "🏆 Leaderboard"]
)

# --------------------------------------------------------------------------- #
# Tab: 3D Tree View
# --------------------------------------------------------------------------- #
with tab_tree:
    pest_mode = "pest_mode" in st.session_state and st.session_state.pest_mode
    render_tree(
        level=tree.level,
        stage=tree.stage,
        shield_active=shield_active,
        pest_mode=pest_mode,
        pest_count=max(2, tree.level // 3 + 2),
        height=560,
        key=f"tree_{team.id}_{tree.level}_{pest_mode}",
    )
    if pest_mode:
        st.info("Tap/click the pests crawling on the trunk to clear them 🐛")
        if st.button("✅ I cleared them (confirm)"):
            complete_task(db, team, user, Role.CLEANER)
            st.session_state.pest_mode = False
            st.success("Pests cleared! Tozalovchi task complete.")
            st.rerun()

# --------------------------------------------------------------------------- #
# Tab: Control Panel (role actions)
# --------------------------------------------------------------------------- #
with tab_tasks:
    st.markdown("### Your Role Actions")
    if not my_roles:
        st.info("You'll be assigned a role once more teammates join, or you'll get all 5 roles solo.")
    for role in my_roles:
        status = task_status(db, team, role)
        badge = {"done": "🟢", "warning": "🟡", "missed": "🔴", "pending": "⚪", "shielded": "🛡️"}[status["state"]]
        st.markdown(f"#### {badge} {ROLE_LABELS[role]} — {status['completions']}/{status['required']} this cycle")

        action_label = {
            Role.WATERER: "💧 Water Tree",
            Role.GARDENER: "🪴 Loosen Soil",
            Role.SUNLIGHT: "☀️ Adjust Sunlight",
            Role.CLEANER: "🐛 Clear Pests (open 3D view)",
            Role.NURTURER: "🎵 Send Love",
        }[role]

        col_a, col_b = st.columns([1, 2])
        with col_a:
            if role == Role.CLEANER:
                if st.button(action_label, key=f"act_{role}"):
                    st.session_state.pest_mode = True
                    st.info("Switch to the 🌳 3D Tree tab to squash the pests!")
            else:
                if st.button(action_label, key=f"act_{role}"):
                    complete_task(db, team, user, role)
                    st.success(f"{ROLE_LABELS[role]} task logged!")
                    st.rerun()
        with col_b:
            st.progress(min(1.0, status["completions"] / status["required"]))
    st.caption(
        f"Tasks reset every {TASK_CYCLE_HOURS}h. A 1h warning phase follows. If every role hits its "
        f"target, the tree levels UP; if any role misses it (and no shield is active), it levels DOWN."
    )

# --------------------------------------------------------------------------- #
# Tab: Team Panel
# --------------------------------------------------------------------------- #
with tab_team:
    st.markdown("### Team Members")
    for member in team.members:
        m_roles = roles_for_user(member)
        role_str = ", ".join(ROLE_LABELS[r] for r in m_roles) if m_roles else "Unassigned"
        cols = st.columns([1, 3, 2])
        with cols[0]:
            st.write("👤")
        with cols[1]:
            st.write(f"**{member.display_name}**")
            st.caption(role_str)
        with cols[2]:
            states = [task_status(db, team, r)["state"] for r in m_roles] if m_roles else []
            dot = {"done": "🟢", "warning": "🟡", "missed": "🔴", "pending": "⚪", "shielded": "🛡️"}
            st.write(" ".join(dot.get(s, "⚪") for s in states) or "—")

    st.divider()
    st.markdown("### Invite Teammates")
    if team.is_full:
        st.success("Your grove is full (5/5)! 🌳")
    else:
        st.code(team.referral_code, language=None)
        st.caption("Share this code — a teammate enters it at login (or on the join screen) to auto-join.")

# --------------------------------------------------------------------------- #
# Tab: Shop (Telegram Stars — Magical Shield)
# --------------------------------------------------------------------------- #
with tab_shop:
    st.markdown("### 🛡️ Magical Shield")
    st.write(
        f"Protects your tree from degradation for **{SHIELD_DURATION_DAYS} days**, "
        f"even if tasks are missed. Costs **{SHIELD_COST_STARS} Telegram Stars**."
    )
    if shield_active and active_shield:
        st.success(f"Shield active until **{active_shield.expires_at.strftime('%Y-%m-%d %H:%M UTC')}**")
    st.markdown(
        "Purchases are completed via the Telegram bot, since in-app Stars payments must be "
        "initiated through the Bot API, not the web view."
    )
    st.write(f"In Telegram, message **@{BOT_USERNAME}** and send:")
    st.code(f"/shield {team.referral_code}", language=None)

# --------------------------------------------------------------------------- #
# Tab: Leaderboard
# --------------------------------------------------------------------------- #
with tab_board:
    st.markdown("### 🏆 Top Groves")
    top = (
        db.query(Team, TreeState)
        .join(TreeState, TreeState.team_id == Team.id)
        .order_by(TreeState.level.desc(), TreeState.xp.desc())
        .limit(20)
        .all()
    )
    for i, (t, ts) in enumerate(top, start=1):
        marker = "👑" if t.id == team.id else f"{i}."
        st.write(f"{marker} **{t.name}** — Level {ts.level} ({ts.xp} XP)")

db.close()
