from __future__ import annotations

import base64
import io
import json
import math
import random
import struct
import wave
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Venom Hangman",
    page_icon= "assets/venom_head.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# DATA
# ============================================================

WORD_BANK = {
    "Movies": [
        "venom", "batman", "avatar", "joker",
        "matrix", "gladiator", "inception",
    ],
    "Animals": [
        "elephant", "giraffe", "dolphin", "penguin",
        "cheetah", "octopus", "tiger", "dog", "cat", "kangaroo"
    ],
    "Programming": [
        "python", "developer", "variable", "function",
        "streamlit", "hangman", "database",
    ],
    "Countries": [
        "ethiopia", "germany", "canada", "brazil",
        "japan", "egypt", "kenya", "australia", "france", "Djibouti",
    ],
}

DIFFICULTY_ATTEMPTS = {
    "Easy": 8,
    "Medium": 6,
    "Hard": 5,
    "pro": 2,
}

CATEGORY_ICONS = {
    "Movies": "📽️",
    "Animals": "🐾",
    "Programming": "💻",
    "Countries": "🌍",
}

HINTS_PER_ROUND = 3

# Three staggered rows (9/9/8) instead of two rows of 13 — this
# is what actually fixes the "doesn't fit / not compatible"
# complaint. st.columns() never wraps on narrow screens, it just
# squeezes every column thinner, so 13-across was guaranteed to
# feel cramped on anything smaller than a wide desktop. Shorter
# rows plus a real mobile breakpoint (below) keep every key at a
# legible tap size.
KEYBOARD_ROWS = (
    "abcdefghi",
    "jklmnopqr",
    "stuvwxyz",
)

BASE_DIR = Path(__file__).resolve().parent
ASSETS = BASE_DIR / "assets"


# ============================================================
# ASSET HELPERS
# ============================================================

def _image_data_uri(path: Path) -> str | None:
    """Inline an image as a base64 data URI so it can be used as
    a real CSS background-image instead of a full-width
    st.image() row that eats the whole layout."""

    if not path.exists():
        return None

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    suffix = path.suffix.lower().lstrip(".")
    mime = "jpeg" if suffix in ("jpg", "jpeg") else (suffix or "png")

    return f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"


ARENA_DATA_URI = _image_data_uri(ASSETS / "arena.png")


def _audio_data_uri(path: Path) -> str | None:
    """Same pattern as _image_data_uri, for real audio assets —
    e.g. a recorded laugh dropped into assets/ instead of a
    synthesized tone."""

    if not path.exists():
        return None

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    suffix = path.suffix.lower().lstrip(".")
    mime = "mpeg" if suffix in ("mp3",) else (suffix or "mpeg")

    return f"data:audio/{mime};base64,{base64.b64encode(raw).decode()}"


LOSE_LAUGH_URI = _audio_data_uri(ASSETS / "lose_laugh.mp3")


# ============================================================
# ADVANCED FEATURE: PERSISTENT LOCAL STATS
# Survives app restarts (not just the browser session) by
# writing a small JSON file next to the script. If the runtime
# filesystem is read-only (e.g. some hosted environments), this
# fails silently and the game simply falls back to in-memory,
# per-session stats — never crashes the app.
# ============================================================

STATS_FILE = BASE_DIR / "venom_stats.json"


def _load_persistent_stats() -> dict:
    try:
        if STATS_FILE.exists():
            return json.loads(STATS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_persistent_stats() -> None:
    try:
        STATS_FILE.write_text(
            json.dumps(
                {
                    "games_played": st.session_state.games_played,
                    "games_won": st.session_state.games_won,
                    "current_streak": st.session_state.current_streak,
                    "best_streak": st.session_state.best_streak,
                }
            )
        )
    except OSError:
        pass


# ============================================================
# ADVANCED FEATURE: SOUND EFFECTS
# Tiny synthesized WAV tones (no external audio files or
# libraries needed) played through a hidden components.html
# <audio> tag, gated by the existing sound_on toggle in the
# sidebar. "correct"/"wrong" are short plain tones; "win"/"lose"
# are built with a more flexible synth (_synth_wav) that supports
# a changing pitch and amplitude over time, used for a short
# victory fanfare and an eerie descending "cackle" effect.
#
# Honest note: these are procedurally generated waveforms, not
# real recorded voice/laughter — there's no licensed sound-effect
# source available to pull an actual voice or laugh clip from
# here, so this is the closest self-contained equivalent rather
# than a literal recording.
# ============================================================

def _tone_data_uri(freq: float, duration: float = 0.14, volume: float = 0.35) -> str:
    framerate = 8000
    n_samples = int(framerate * duration)
    buf = io.BytesIO()

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)

        for i in range(n_samples):
            t = i / framerate
            envelope = 1.0 - (i / n_samples)  # fade-out avoids a click at the end
            sample = volume * envelope * math.sin(2 * math.pi * freq * t)
            wf.writeframesraw(struct.pack("<h", int(sample * 32767)))

    return f"data:audio/wav;base64,{base64.b64encode(buf.getvalue()).decode()}"


def _synth_wav(duration: float, freq_fn, amp_fn, framerate: int = 8000) -> str:
    """Generic synthesizer: freq_fn(t) and amp_fn(t) return the
    instantaneous frequency (Hz) and amplitude (0-1) at time t,
    letting pitch and volume both change over the clip — used for
    the fanfare (stepped pitch) and the cackle (wobbling pitch +
    pulsing volume) below."""

    n_samples = int(framerate * duration)
    buf = io.BytesIO()

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)

        phase = 0.0
        for i in range(n_samples):
            t = i / framerate
            phase += 2 * math.pi * freq_fn(t) / framerate
            sample = amp_fn(t) * math.sin(phase)
            sample = max(-1.0, min(1.0, sample))
            wf.writeframesraw(struct.pack("<h", int(sample * 32767)))

    return f"data:audio/wav;base64,{base64.b64encode(buf.getvalue()).decode()}"


def _win_fanfare_uri() -> str:
    """A short rising four-note arpeggio — a synthesized stand-in
    for a celebratory 'ta-da' sting."""

    notes = [523.25, 659.25, 783.99, 1046.50]  # C5, E5, G5, C6
    note_dur = 0.12
    total = note_dur * len(notes)

    def freq_fn(t: float) -> float:
        idx = min(int(t / note_dur), len(notes) - 1)
        return notes[idx]

    def amp_fn(t: float) -> float:
        local_t = t % note_dur
        attack = min(1.0, local_t / 0.01)
        release = min(1.0, (note_dur - local_t) / 0.02)
        fade_out = 1.0 if t < total - 0.05 else max(0.0, (total - t) / 0.05)
        return 0.32 * attack * release * fade_out

    return _synth_wav(total, freq_fn, amp_fn)


def _lose_cackle_uri() -> str:
    """A wobbling, descending tone with a pulsing rhythm — an
    eerie synthesized 'cackle' effect rather than a real laugh
    recording."""

    duration = 1.15

    def freq_fn(t: float) -> float:
        base = 260 - 140 * (t / duration)  # descends 260Hz -> 120Hz
        vibrato = 18 * math.sin(2 * math.pi * 7 * t)
        return max(60.0, base + vibrato)

    def amp_fn(t: float) -> float:
        tremolo = 0.5 + 0.5 * math.sin(2 * math.pi * 5.5 * t - math.pi / 2)
        fade_in = min(1.0, t / 0.03)
        fade_out = 1.0 if t < duration - 0.15 else max(0.0, (duration - t) / 0.15)
        return 0.38 * tremolo * fade_in * fade_out

    return _synth_wav(duration, freq_fn, amp_fn)


SOUND_TONES = {
    "correct": _tone_data_uri(660, 0.10),
    "wrong": _tone_data_uri(180, 0.18),
    "win": _win_fanfare_uri(),
    # Real recorded laugh if assets/lose_laugh.mp3 is present,
    # falling back to the synthesized cackle otherwise so the
    # game still works if the asset is missing.
    "lose": LOSE_LAUGH_URI if LOSE_LAUGH_URI else _lose_cackle_uri(),
}


def render_sound() -> None:
    """Plays (at most) one sound per real game action, then
    clears the event so it never replays on an unrelated rerun
    (e.g. flipping to the Settings page and back).

    IMPORTANT: this must render *something* at this exact script
    position on every single run, never zero elements sometimes
    and one element other times. Streamlit's components.html()
    isn't individually keyed, so it's tracked by position — if
    the element count at this spot changes between reruns (which
    it did before this fix, since it only emitted an <audio> tag
    when there was a sound to play), everything rendered after it
    on the page gets misaligned during reconciliation instead of
    replacing the previous run's content — which is what produced
    the stacked/duplicated HUD and keyboard blocks. A stable
    st.empty() placeholder, always present and simply re-filled or
    cleared, fixes that at the source."""

    event = st.session_state.get("sound_event")
    st.session_state.sound_event = None

    slot = st.empty()

    uri = SOUND_TONES.get(event) if (event and st.session_state.sound_on) else None

    if uri:
        with slot:
            components.html(f'<audio autoplay src="{uri}"></audio>', height=0)
    else:
        slot.empty()


# ============================================================
# ADVANCED FEATURE: PHYSICAL KEYBOARD SUPPORT
# Lets the player type instead of only clicking on-screen keys.
# Runs inside Streamlit's own component iframe, which is
# same-origin, so it can reach back into the parent page and
# simulate a real click on the matching live key. Streamlit then
# sees an ordinary button click — nothing custom needed on the
# Python side to receive it. (Restored here after it was lost in
# an earlier edit — the selector below matches the current stable
# kbd_slot_<letter> container keys.)
# ============================================================

def render_keyboard_bridge() -> None:
    components.html(
        """
        <script>
        (function () {
            const doc = window.parent.document;

            function handleKey(e) {
                const letter = e.key.toLowerCase();
                if (letter.length !== 1 || letter < 'a' || letter > 'z') return;

                const wrap = doc.querySelector(
                    'div[class*="st-key-kbd_slot_' + letter + '"]'
                );
                if (!wrap) return;

                const btn = wrap.querySelector('button');
                if (btn && !btn.disabled) btn.click();
            }

            // Re-registering on every rerun would stack duplicate
            // listeners (one extra guess per keypress per rerun),
            // so the previous handler is removed first.
            if (window.__venomKeyHandler) {
                doc.removeEventListener('keydown', window.__venomKeyHandler);
            }
            window.__venomKeyHandler = handleKey;
            doc.addEventListener('keydown', window.__venomKeyHandler);
        })();
        </script>
        """,
        height=0,
    )


# ============================================================
# ADVANCED FEATURE: WIN FIREWORKS
# A short full-screen particle-burst overlay on victory. This
# follows the same stable-placeholder rule as render_sound() and
# the result banner: the wrapping container is ALWAYS created
# every run (so its position never shifts), and only the content
# inside a nested st.empty() placeholder is conditional — this is
# what keeps it from reintroducing the duplication bug fixed
# earlier. It plays once per win (fireworks_shown gates a repeat
# on unrelated reruns) and cleans itself up afterward.
# ============================================================

_FIREWORKS_HTML = """
<canvas id="fw" style="position:fixed;inset:0;width:100vw;height:100vh;"></canvas>
<script>
(function () {
    const canvas = document.getElementById('fw');
    const ctx = canvas.getContext('2d');

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const colors = ['#A6FF1E', '#eafff0', '#ffd25a', '#ff8792'];
    let particles = [];

    function burst(x, y) {
        const count = 42;
        for (let i = 0; i < count; i++) {
            const angle = (Math.PI * 2 * i) / count;
            const speed = 2 + Math.random() * 3.2;
            particles.push({
                x, y,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed,
                life: 55 + Math.random() * 20,
                maxLife: 75,
                color: colors[Math.floor(Math.random() * colors.length)],
            });
        }
    }

    const bursts = [
        { t: 0,  x: 0.28, y: 0.28 },
        { t: 14, x: 0.72, y: 0.24 },
        { t: 30, x: 0.50, y: 0.38 },
        { t: 46, x: 0.20, y: 0.34 },
        { t: 62, x: 0.80, y: 0.36 },
    ];

    let elapsed = 0;

    function frame() {
        elapsed++;
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        bursts.forEach(function (b) {
            if (elapsed === b.t) burst(b.x * canvas.width, b.y * canvas.height);
        });

        particles = particles.filter(function (p) { return p.life > 0; });
        particles.forEach(function (p) {
            p.x += p.vx;
            p.y += p.vy;
            p.vy += 0.05;
            p.life--;
            ctx.globalAlpha = Math.max(0, p.life / p.maxLife);
            ctx.fillStyle = p.color;
            ctx.beginPath();
            ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
            ctx.fill();
        });
        ctx.globalAlpha = 1;

        if (elapsed < 160) {
            requestAnimationFrame(frame);
        } else {
            canvas.remove();
        }
    }
    requestAnimationFrame(frame);
})();
</script>
"""


def render_fireworks() -> None:
    with st.container(key="fireworks_overlay"):
        slot = st.empty()

        show = (
            st.session_state.won
            and not st.session_state.get("fireworks_shown", False)
        )

        if show:
            st.session_state.fireworks_shown = True
            with slot:
                components.html(_FIREWORKS_HTML, height=0)
        else:
            slot.empty()


# ============================================================
# ADVANCED FEATURE: ACHIEVEMENTS
# ============================================================

def get_achievements() -> list[str]:
    badges = []

    if st.session_state.current_streak >= 3:
        badges.append("🔥 On Fire — 3-win streak")

    if st.session_state.current_streak >= 5:
        badges.append("🏆 Venom Slayer — 5-win streak")

    if st.session_state.best_streak >= 10:
        badges.append("☠ Lethal Protector — 10-win streak")

    if st.session_state.games_won >= 10:
        badges.append("🧬 Symbiote Bond — 10 total wins")

    if (
        st.session_state.games_played >= 3
        and st.session_state.games_won == st.session_state.games_played
    ):
        badges.append("💯 Flawless — no losses yet")

    return badges


# ============================================================
# SESSION STATE
# ============================================================

def start_new_game(
    category: str | None = None,
    difficulty: str | None = None,
) -> None:

    if category is not None:
        st.session_state.category = category

    if difficulty is not None:
        st.session_state.difficulty = difficulty

    word = random.choice(
        WORD_BANK[st.session_state.category]
    ).lower()

    st.session_state.word = word
    st.session_state.used_letters = set()
    st.session_state.wrong_letters = set()

    st.session_state.max_attempts = (
        DIFFICULTY_ATTEMPTS[
            st.session_state.difficulty
        ]
    )

    st.session_state.attempts_left = (
        st.session_state.max_attempts
    )

    st.session_state.hints_left = HINTS_PER_ROUND

    st.session_state.game_over = False
    st.session_state.won = False
    st.session_state.gave_up = False

    # Prevent repeated statistics during Streamlit reruns.
    st.session_state.round_recorded = False

    # Lets the win-fireworks play exactly once for this round.
    st.session_state.fireworks_shown = False


def initialize_state() -> None:

    persisted = _load_persistent_stats()

    defaults = {
        "category": "Movies",
        "difficulty": "Hard",
        "games_played": persisted.get("games_played", 0),
        "games_won": persisted.get("games_won", 0),
        "current_streak": persisted.get("current_streak", 0),
        "best_streak": persisted.get("best_streak", 0),
        "page": "game",
        "sound_on": True,
        "sound_event": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if "word" not in st.session_state:
        start_new_game()


def record_result(won: bool) -> None:

    if st.session_state.round_recorded:
        return

    st.session_state.games_played += 1

    if won:
        st.session_state.games_won += 1
        st.session_state.current_streak += 1
        st.session_state.best_streak = max(
            st.session_state.best_streak,
            st.session_state.current_streak,
        )
    else:
        st.session_state.current_streak = 0

    st.session_state.round_recorded = True
    _save_persistent_stats()


def word_is_solved() -> bool:

    return all(
        letter in st.session_state.used_letters
        for letter in st.session_state.word
    )


def evaluate_game() -> None:

    if st.session_state.game_over:
        return

    if word_is_solved():
        st.session_state.won = True
        st.session_state.game_over = True
        st.session_state.sound_event = "win"
        record_result(True)
        return

    if st.session_state.attempts_left <= 0:
        st.session_state.attempts_left = 0
        st.session_state.game_over = True
        st.session_state.sound_event = "lose"
        record_result(False)


def guess_letter(letter: str) -> None:

    if st.session_state.game_over:
        return

    if letter in st.session_state.used_letters:
        return

    st.session_state.used_letters.add(letter)

    if letter not in st.session_state.word:
        st.session_state.wrong_letters.add(letter)
        st.session_state.attempts_left -= 1
        st.session_state.sound_event = "wrong"
    else:
        st.session_state.sound_event = "correct"

    evaluate_game()


def use_hint() -> None:

    if st.session_state.game_over:
        return

    if st.session_state.hints_left <= 0:
        return

    hidden = [
        letter
        for letter in st.session_state.word
        if letter not in st.session_state.used_letters
    ]

    if not hidden:
        evaluate_game()
        return

    letter = random.choice(hidden)

    st.session_state.used_letters.add(letter)
    st.session_state.hints_left -= 1

    evaluate_game()


def give_up() -> None:

    if st.session_state.game_over:
        return

    st.session_state.gave_up = True
    st.session_state.game_over = True
    st.session_state.sound_event = "lose"
    record_result(False)


def toggle_sound() -> None:
    st.session_state.sound_on = not st.session_state.sound_on


def on_category_change() -> None:
    start_new_game(category=st.session_state.category)


def on_difficulty_change() -> None:
    start_new_game(difficulty=st.session_state.difficulty)


def _drip_word(word: str) -> str:
    """Wrap each letter of a word in its own span so CSS can give
    every letter a slightly different vertical offset and tilt —
    a static, no-motion 'oozing symbiote lettering' effect. This
    is plain text content we control (not user input), so it's
    safe to build as markup."""

    return "".join(
        f'<span class="drip-letter d{i % 3}">{ch}</span>'
        for i, ch in enumerate(word)
    )


def render_header(second_word: str, tagline: str) -> None:
    """Shared page header: the VENOM wordmark always gets the
    drippy green treatment; the second word stays plain white so
    the brand mark is the one loud element per page, not two."""

    st.markdown(
        f"""
        <div class="app-name">
            <h1><span class="venom-word">{_drip_word('VENOM')}</span> {second_word}</h1>
            <p>{tagline}</p>
            <div class="drip-edge"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


initialize_state()


# ============================================================
# CSS
# IMPORTANT:
# All visible UI below uses native Streamlit components.
# No dynamic HTML is generated for content, so <div>/<span>
# cannot leak into the screen as text. Styling hooks below use
# Streamlit's supported st.container(key=...) mechanism, which
# renders as a stable `.st-key-<name>` class on that container
# — this lets specific groups of buttons be themed differently
# without any raw HTML nesting tricks.
# ============================================================

st.markdown(
    """
    <style>

    @import url(
        'https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Nosifer&family=Inter:wght@400;500;600;700;800;900&display=swap'
    );

    /* ---------- GLOBAL ---------- */

    html, body, [class*="css"] {
        font-family: "Inter", sans-serif;
    }

    .stApp {
        background: #060807;
        color: #f2f2f2;
        overflow-x: hidden;
    }

    [data-testid="stHeader"] { display: none; }
    #MainMenu, footer { visibility: hidden; }

    /* ---------- FULL-PAGE ARENA BACKGROUND ---------- */
    /* Applied to the main content area only (stMain), so the
       sidebar keeps its own solid panel treatment below and is
       never covered by the arena artwork. Default here is a
       designed toxic gradient; if assets/arena.png exists, a
       second <style> block injected after this one (see
       ARENA_DATA_URI below) layers the real photo underneath
       the same dark gradient so text stays legible either way. */

    [data-testid="stMain"],
    div.main {
        background:
            linear-gradient(180deg, rgba(4,5,4,.34), rgba(3,4,3,.90) 75%),
            radial-gradient(circle at 22% 12%, rgba(166,255,30,.10), transparent 45%),
            radial-gradient(circle at 85% 92%, rgba(166,255,30,.06), transparent 45%),
            linear-gradient(165deg, #0e120e, #050605 85%);
        background-size: cover;
        background-position: center 28%;
        background-repeat: no-repeat;
        background-attachment: fixed;
    }

    @media (max-width: 720px) {
        [data-testid="stMain"], div.main {
            background-attachment: scroll;
        }
    }

    /* Fit-everything: a tighter, centered column instead of a
       1500px stretch that leaves huge dead space and forces
       extra scrolling to reach the keyboard. */
    .block-container {
        max-width: 1040px;
        padding-top: 10px;
        padding-bottom: 18px;
    }

    /* ---------- SIDEBAR ---------- */

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #060807, #0a0d0b 55%, #030402) !important;
        border-right: 1px solid rgba(166,255,30,.16);
    }

    section[data-testid="stSidebar"] > div { padding: 14px 12px 20px; }

    section[data-testid="stSidebar"] .stButton > button {
        min-height: 40px;
        background: linear-gradient(145deg, #12160f, #080a08) !important;
        color: #dddddd !important;
        border: 1px solid rgba(255,255,255,.08) !important;
        border-radius: 10px !important;
        font-weight: 800 !important;
    }

    section[data-testid="stSidebar"] .stButton > button:hover {
        color: #A6FF1E !important;
        border-color: #A6FF1E !important;
        box-shadow: 0 0 12px rgba(166,255,30,.14);
    }

    .side-panel {
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px solid rgba(166,255,30,.16);
        background: rgba(10,13,11,.75);
        margin-top: 10px;
    }

    .small-green {
        color: #A6FF1E;
        font-weight: 900;
        letter-spacing: 2px;
        font-size: 10px;
    }

    .dev-credit {
        text-align: center;
        font-size: 9px;
        letter-spacing: 1px;
        color: #5c645c;
        margin-top: 14px;
    }

    .dev-credit span {
        color: #A6FF1E;
        font-weight: 800;
    }

    /* ---------- TOP HEADER ---------- */

    .app-name { text-align: center; margin-bottom: 4px; }

    .app-name h1 {
        font-family: "Bebas Neue", sans-serif;
        font-size: 32px;
        letter-spacing: 4px;
        margin: 0;
        color: #fff;
    }

    .app-name p {
        color: #9aa39c;
        font-size: 9px;
        letter-spacing: 3px;
        margin-top: -2px;
        text-shadow: 0 1px 3px rgba(0,0,0,.8);
    }

    /* ---------- VENOM DRIP LETTERING ---------- */
    /* Each letter of "VENOM" is its own span so it can carry a
       slightly different tilt/lift — a static, no-animation take
       on symbiote lettering: a toxic glow plus a dark ink
       underlay so it still reads clearly over the photo. */

    .venom-word { display: inline-block; }

    .drip-letter {
        display: inline-block;
        color: #A6FF1E;
        text-shadow:
            0 0 4px rgba(166,255,30,.95),
            0 0 18px rgba(166,255,30,.55),
            0 2px 0 rgba(4,7,4,.95);
    }

    .drip-letter.d0 { transform: translateY(-2px) rotate(-2deg); }
    .drip-letter.d1 { transform: translateY(2px) rotate(1.5deg); }
    .drip-letter.d2 { transform: translateY(-1px) rotate(-0.5deg); }

    .drip-edge {
        height: 11px;
        margin: 6px auto 0;
        max-width: 230px;
        background-image: radial-gradient(circle at 8px 0, #A6FF1E 5px, transparent 6px);
        background-size: 18px 11px;
        background-repeat: repeat-x;
        opacity: .55;
        filter: drop-shadow(0 3px 2px rgba(166,255,30,.35));
    }

    /* ---------- SELECTS ---------- */

    div[data-baseweb="select"] > div {
        background: #0a0d0b !important;
        border: 1px solid rgba(166,255,30,.20) !important;
        border-radius: 10px !important;
        min-height: 38px !important;
    }

    label {
        color: #707770 !important;
        font-size: 9px !important;
        letter-spacing: 2px !important;
        font-weight: 900 !important;
    }

    /* "GUESS THE WORD" — horror/symbiote display lettering per
       the reference: bold, irregular/organic glyphs (Nosifer has
       a dripping edge baked into the font itself), strong green
       glow, whole phrase treated the same rather than split into
       two colors. */

    .panel-title {
        text-align: center;
        font-family: "Nosifer", "Bebas Neue", sans-serif;
        color: #eafff0;
        letter-spacing: 1px;
        font-size: 22px;
        margin: 10px 0 10px;
        line-height: 1.4;
        text-shadow:
            0 0 6px rgba(166,255,30,.95),
            0 0 16px rgba(166,255,30,.8),
            0 0 34px rgba(166,255,30,.45),
            0 3px 0 rgba(3,5,3,.9);
    }

    /* ---------- WIN FIREWORKS OVERLAY ---------- */
    /* Forces the (usually empty) fireworks container to sit as a
       full-screen, click-through layer above everything else only
       for the brief moment it actually has content — harmless and
       invisible the rest of the time since it's empty and
       non-interactive. */

    div[class*="st-key-fireworks_overlay"] {
        position: fixed !important;
        inset: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        pointer-events: none !important;
        z-index: 9999 !important;
    }

    div[class*="st-key-fireworks_overlay"] iframe {
        width: 100vw !important;
        height: 100vh !important;
        border: none !important;
        background: transparent !important;
    }

    /* ---------- ARENA HUD (frosted panel over the page bg) ---------- */
    /* The arena artwork now lives on the whole page (stMain), so
       this panel is a translucent, blurred glass card floating
       on top of it rather than repeating the image — keeps the
       health/word readout legible without looking like a second,
       competing background. */

    div[class*="st-key-arena_hud"] {
        position: relative;
        border-radius: 18px;
        overflow: hidden;
        padding: 14px 16px 10px;
        margin: 8px 0 12px;
        min-height: 150px;
        border: 1px solid rgba(166,255,30,.28);
        background: rgba(6,9,7,.52);
        backdrop-filter: blur(6px) saturate(1.1);
        -webkit-backdrop-filter: blur(6px) saturate(1.1);
        box-shadow:
            inset 0 0 40px rgba(0,0,0,.35),
            0 0 0 1px rgba(166,255,30,.06),
            0 10px 26px rgba(0,0,0,.4);
    }

    .hud-row {
        position: relative;
        z-index: 1;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 6px;
    }

    .hud-caption {
        color: #A6FF1E;
        font-size: 10px;
        font-weight: 900;
        letter-spacing: 2.5px;
    }

    /* "TRIES LEFT: ❤❤❤🖤🖤" — plain text + glowing hearts, matching
       the reference design (no pill/badge background). */

    .tries-label {
        font-weight: 900;
        font-size: 11px;
        letter-spacing: 1.5px;
        color: #A6FF1E;
        white-space: nowrap;
        text-shadow: 0 1px 3px rgba(0,0,0,.8);
    }

    .tries-label .hearts { margin-left: 6px; }

    .heart-full {
        color: #ff3b4e;
        text-shadow:
            0 0 6px rgba(255,59,78,.95),
            0 0 14px rgba(255,59,78,.55);
        font-size: 15px;
    }

    .heart-empty {
        color: #4a1c20;
        text-shadow: none;
        font-size: 15px;
        opacity: .7;
    }

    /* Word-slot tiles rendered inside the HUD — matches the JOKER
       reference: dark green/black tile, subtle full border (not
       just an underline), bright toxic-green horror lettering
       with a strong glow for contrast against the dark tile. */

    div[class*="st-key-word_row"] .stButton > button {
        position: relative;
        z-index: 1;
        min-height: 58px !important;
        background: linear-gradient(165deg, rgba(19,26,17,.92), rgba(7,10,7,.96)) !important;
        border: 1px solid rgba(166,255,30,.38) !important;
        border-radius: 10px !important;
        color: #A6FF1E !important;
        font-family: "Nosifer", "Bebas Neue", sans-serif !important;
        font-size: 19px !important;
        letter-spacing: 1px !important;
        opacity: 1 !important;
        text-shadow:
            0 0 5px rgba(166,255,30,.95),
            0 0 13px rgba(166,255,30,.55);
        box-shadow:
            inset 0 0 12px rgba(0,0,0,.55),
            0 4px 10px rgba(0,0,0,.35);
    }

    /* ---------- RESULT / STATUS TEXT ---------- */

    .stAlert { border-radius: 12px; }

    /* ---------- KEYBOARD ---------- */

    div[class*="st-key-kbd_panel"] {
        position: relative;
        padding: 16px 10px 8px;
        border-radius: 16px;
        background: linear-gradient(165deg, rgba(13,16,12,.94), rgba(4,5,4,.97));
        border: 1px solid rgba(166,255,30,.12);
        margin-top: 10px;
    }

    /* A thin row of toxic "drip" blobs along the top edge, as if
       the keyboard is emerging from the same ooze as the title —
       one consistent decorative motif reused, not scattered. */
    div[class*="st-key-kbd_panel"]::before {
        content: "";
        position: absolute;
        top: -6px;
        left: 12px;
        right: 12px;
        height: 10px;
        background-image: radial-gradient(circle at 6px 0, #A6FF1E 4px, transparent 5px);
        background-size: 16px 10px;
        background-repeat: repeat-x;
        opacity: .4;
        pointer-events: none;
    }

    div[class*="st-key-kbd_row_"] { margin-bottom: 6px; }

    /* All three key states below are scoped to the keyboard panel
       and keyed off Streamlit's own button `type=`, which is safe
       to change on an otherwise stable widget key (unlike the
       per-state container keys used before, which caused the
       duplicate/ghost keyboard rows). */

    /* Live / guessable key: bright bone surface, unmistakably
       tappable against the dark page. */

    div[class*="st-key-kbd_panel"] [data-testid="stBaseButton-secondary"],
    div[class*="st-key-kbd_panel"] button[kind="secondary"] {
        min-height: 46px !important;
        background:
            linear-gradient(180deg, rgba(255,255,255,.4), rgba(255,255,255,0) 45%),
            #ECEFE7 !important;
        color: #0c0f0a !important;
        border: 2px solid #14170f !important;
        border-radius: 10px !important;
        font-weight: 900 !important;
        font-size: 15px !important;
        box-shadow: 0 3px 0 rgba(0,0,0,.4);
        transition: transform .08s ease, box-shadow .12s ease, background .12s ease;
    }

    div[class*="st-key-kbd_panel"] [data-testid="stBaseButton-secondary"]:hover:not(:disabled),
    div[class*="st-key-kbd_panel"] button[kind="secondary"]:hover:not(:disabled) {
        background:
            linear-gradient(180deg, rgba(255,255,255,.3), rgba(255,255,255,0) 45%),
            #A6FF1E !important;
        border-color: #A6FF1E !important;
        color: #04140a !important;
        transform: translateY(-2px);
        box-shadow: 0 0 16px rgba(166,255,30,.55);
    }

    /* Correct guess: solid filled state (Wordle/Duolingo-style key
       feedback), boosted with a glossy top-highlight so it reads
       as the tile you want to look at — this is the state that
       should visually "win" the eye's attention on the board. */

    div[class*="st-key-kbd_panel"] [data-testid="stBaseButton-primary"],
    div[class*="st-key-kbd_panel"] button[kind="primary"] {
        position: relative;
        min-height: 46px !important;
        background:
            linear-gradient(180deg, rgba(255,255,255,.35), rgba(255,255,255,0) 45%),
            linear-gradient(145deg, #9dff3e, #4fa30d) !important;
        color: #04140a !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 900 !important;
        font-size: 15px !important;
        opacity: 1 !important;
        box-shadow: 0 3px 0 rgba(0,60,0,.45), 0 0 12px rgba(166,255,30,.35);
    }

    div[class*="st-key-kbd_panel"] [data-testid="stBaseButton-primary"]::after,
    div[class*="st-key-kbd_panel"] button[kind="primary"]::after {
        content: "✓";
        position: absolute;
        top: -7px;
        right: -7px;
        width: 18px;
        height: 18px;
        line-height: 18px;
        font-size: 11px;
        font-weight: 900;
        text-align: center;
        color: #04140a;
        background: #eafff0;
        border-radius: 50%;
        box-shadow: 0 0 0 2px #0d100e, 0 0 6px rgba(166,255,30,.5);
    }

    /* Wrong guess: muted, "spent" — a small X badge instead of a
       strikethrough, so it still reads clearly as a real key rather
       than crossed-out text. Deliberately understated next to the
       vivid correct-guess tiles, so the eye is drawn to progress
       rather than misses. */

    div[class*="st-key-kbd_panel"] [data-testid="stBaseButton-tertiary"],
    div[class*="st-key-kbd_panel"] button[kind="tertiary"] {
        position: relative;
        min-height: 46px !important;
        background: linear-gradient(165deg, #241417, #170c0e) !important;
        color: #8f5a60 !important;
        border: 1px solid rgba(255,59,78,.35) !important;
        border-radius: 10px !important;
        font-weight: 800 !important;
        font-size: 15px !important;
        opacity: 1 !important;
    }

    div[class*="st-key-kbd_panel"] [data-testid="stBaseButton-tertiary"]::after,
    div[class*="st-key-kbd_panel"] button[kind="tertiary"]::after {
        content: "✕";
        position: absolute;
        top: -7px;
        right: -7px;
        width: 18px;
        height: 18px;
        line-height: 18px;
        font-size: 10px;
        font-weight: 900;
        text-align: center;
        color: #fff;
        background: #ff3b4e;
        border-radius: 50%;
        box-shadow: 0 0 0 2px #0d100e, 0 0 6px rgba(255,59,78,.5);
    }

    /* ---------- ACTION BUTTONS (distinct identity per action) ---------- */

    div[class*="st-key-btn_hint"] .stButton > button {
        min-height: 44px !important;
        border-radius: 10px !important;
        font-weight: 900 !important;
        background: linear-gradient(145deg, #2e2408, #14100a) !important;
        color: #ffd25a !important;
        border: 1px solid rgba(255,210,90,.45) !important;
        transition: box-shadow .12s ease, transform .1s ease;
    }

    div[class*="st-key-btn_hint"] .stButton > button:hover:not(:disabled) {
        border-color: #ffd25a !important;
        color: #fff2c8 !important;
        box-shadow: 0 0 14px rgba(255,210,90,.4);
        transform: translateY(-1px);
    }

    div[class*="st-key-btn_giveup"] .stButton > button {
        min-height: 44px !important;
        border-radius: 10px !important;
        font-weight: 900 !important;
        background: linear-gradient(145deg, #2b0d10, #150606) !important;
        color: #ff8792 !important;
        border: 1px solid rgba(255,59,78,.45) !important;
        transition: box-shadow .12s ease, transform .1s ease;
    }

    div[class*="st-key-btn_giveup"] .stButton > button:hover:not(:disabled) {
        border-color: #ff3b4e !important;
        color: #ffd0d4 !important;
        box-shadow: 0 0 14px rgba(255,59,78,.4);
        transform: translateY(-1px);
    }

    div[class*="st-key-btn_play_again"] .stButton > button {
        min-height: 52px !important;
        font-size: 16px !important;
        font-weight: 900 !important;
        letter-spacing: 1px;
        background: linear-gradient(120deg, #A6FF1E, #7fe600) !important;
        color: #04140a !important;
        border: none !important;
        border-radius: 12px !important;
        box-shadow: 0 0 20px rgba(166,255,30,.4), 0 6px 0 rgba(0,0,0,.35) !important;
        transition: box-shadow .12s ease, transform .1s ease;
    }

    div[class*="st-key-btn_play_again"] .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 0 26px rgba(166,255,30,.55), 0 8px 0 rgba(0,0,0,.35) !important;
    }

    /* ---------- SIDEBAR CTA + NAV ---------- */

    div[class*="st-key-sidebar_cta"] .stButton > button {
        background: linear-gradient(120deg, #A6FF1E, #7fe600) !important;
        color: #04140a !important;
        border: none !important;
        font-weight: 900 !important;
        letter-spacing: 1px;
        box-shadow: 0 0 14px rgba(166,255,30,.35), 0 3px 0 rgba(0,0,0,.3) !important;
        transition: box-shadow .12s ease, transform .1s ease;
    }

    div[class*="st-key-sidebar_cta"] .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 0 20px rgba(166,255,30,.5), 0 4px 0 rgba(0,0,0,.3) !important;
    }

    div[class*="st-key-nav_category"] .stButton > button,
    div[class*="st-key-nav_stats"] .stButton > button,
    div[class*="st-key-nav_settings"] .stButton > button {
        border-left: 3px solid transparent !important;
        text-align: left !important;
        padding-left: 12px !important;
        transition: border-color .12s ease, padding-left .12s ease, color .12s ease;
    }

    div[class*="st-key-nav_category"] .stButton > button:hover,
    div[class*="st-key-nav_stats"] .stButton > button:hover,
    div[class*="st-key-nav_settings"] .stButton > button:hover {
        border-left-color: #A6FF1E !important;
        padding-left: 16px !important;
    }

    /* ---------- CATEGORY CARDS ---------- */

    div[class*="st-key-cat_card_"] {
        position: relative;
        background: linear-gradient(165deg, rgba(17,21,16,.92), rgba(6,8,6,.96));
        border: 1px solid rgba(166,255,30,.18);
        border-radius: 16px;
        padding: 18px 10px 14px;
        margin-bottom: 16px;
        text-align: center;
        transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease;
    }

    div[class*="st-key-cat_card_"]:hover {
        transform: translateY(-4px);
        border-color: rgba(166,255,30,.55);
        box-shadow: 0 14px 26px rgba(0,0,0,.4), 0 0 20px rgba(166,255,30,.16);
    }

    div[class*="st-key-cat_card_"] .stButton > button {
        background: transparent !important;
        border: none !important;
        color: #ffffff !important;
        font-family: "Bebas Neue", sans-serif !important;
        font-size: 21px !important;
        letter-spacing: 2px !important;
        min-height: 58px !important;
        box-shadow: none !important;
    }

    div[class*="st-key-cat_card_"] .stButton > button:hover {
        color: #A6FF1E !important;
        text-shadow: 0 0 12px rgba(166,255,30,.55);
    }

    .card-tag {
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 2px;
        color: #6d746e;
        margin-top: 2px;
    }

    .card-tag.active {
        color: #A6FF1E;
        text-shadow: 0 0 8px rgba(166,255,30,.5);
    }

    /* ---------- MANIFESTO ---------- */

    .manifesto {
        margin-top: 14px;
        padding-top: 10px;
        border-top: 1px solid rgba(166,255,30,.18);
        text-align: center;
    }

    .manifesto-line {
        font-family: "Bebas Neue", sans-serif;
        font-size: 15px;
        letter-spacing: 2px;
        color: #cfd6cf;
        line-height: 1.3;
    }

    .manifesto-line span {
        color: #A6FF1E;
        text-shadow: 0 0 8px rgba(166,255,30,.6);
    }

    /* ---------- MOBILE ---------- */

    @media (max-width: 720px) {

        .app-name h1 { font-size: 24px; letter-spacing: 2px; }
        .panel-title { font-size: 16px; }

        div[class*="st-key-arena_hud"] { min-height: 110px; padding: 10px 10px 6px; }

        div[class*="st-key-word_row"] .stButton > button {
            min-height: 40px !important;
            font-size: 17px !important;
        }

        div[class*="st-key-kbd_panel"] [data-testid^="stBaseButton-"],
        div[class*="st-key-kbd_panel"] button[kind] {
            min-height: 36px !important;
            font-size: 11px !important;
            border-radius: 8px !important;
        }
    }

    /* Small phones: stack anything laid out as side-by-side
       columns (category/difficulty selects, hint/give-up, sidebar
       nav pairs, category cards) into a single column instead of
       squeezing them, so nothing gets clipped or unreadable. */

    @media (max-width: 480px) {

        /* Stack side-by-side controls (category/difficulty select,
           hint/give-up, sidebar nav pairs, stats metrics) into a
           single column so nothing gets clipped on tiny screens. */
        div[data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
            row-gap: 10px;
        }

        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            min-width: 100% !important;
            flex: 1 1 100% !important;
        }

        /* ...but NOT the keyboard grid or the word-letter row —
           those must stay as a fixed grid of many small tiles,
           not stack one-per-line, or the game becomes unusable. */
        div[class*="st-key-kbd_panel"] div[data-testid="stHorizontalBlock"],
        div[class*="st-key-word_row"] div[data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
        }

        div[class*="st-key-kbd_panel"] div[data-testid="stColumn"],
        div[class*="st-key-word_row"] div[data-testid="stColumn"] {
            min-width: 0 !important;
            flex: 1 1 0 !important;
        }

        .block-container {
            padding-left: 12px !important;
            padding-right: 12px !important;
        }

        div[class*="st-key-kbd_row_"] { row-gap: 4px; }
    }

    </style>
    """,
    unsafe_allow_html=True,
)

# Layer the real arena.png (if present) across the whole main
# content area — everything except the sidebar, which keeps its
# own solid dark panel. If the asset is missing, the designed
# gradient defined above is used as-is, so the page still looks
# intentional either way.
if ARENA_DATA_URI:
    st.markdown(
        f"""
        <style>
        [data-testid="stMain"], div.main {{
            background-image:
                linear-gradient(180deg, rgba(4,5,4,.34), rgba(3,4,3,.90) 75%),
                url('{ARENA_DATA_URI}');
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    logo_path = ASSETS / "logo.png"

    if logo_path.exists():
        st.image(str(logo_path), use_container_width=True)
    else:
        st.markdown("# VENOM")

    with st.container(key="sidebar_cta"):
        if st.button("🎮  NEW GAME", use_container_width=True, key="new_game_side"):
            start_new_game()
            st.session_state.page = "game"
            st.rerun()

    nav1, nav2 = st.columns(2)

    with nav1:
        with st.container(key="nav_category"):
            if st.button("▦ CATEGORY", use_container_width=True):
                st.session_state.page = "category"
                st.rerun()

    with nav2:
        with st.container(key="nav_stats"):
            if st.button("◢ STATS", use_container_width=True):
                st.session_state.page = "stats"
                st.rerun()

    with st.container(key="nav_settings"):
        if st.button("⚙ SETTINGS", use_container_width=True):
            st.session_state.page = "settings"
            st.rerun()

    games = st.session_state.games_played
    wins = st.session_state.games_won
    win_rate = round((wins / games) * 100) if games else 0

    st.markdown(
        """
        <div class="side-panel">
            <div class="small-green">GAME STATS</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.metric("Games Played", games)
    st.metric("Games Won", wins)
    st.metric("Win Rate", f"{win_rate}%")
    st.metric("Current Streak", st.session_state.current_streak)
    st.metric("Best Streak", st.session_state.best_streak)

    badges = get_achievements()
    if badges:
        st.markdown(
            '<div class="side-panel"><div class="small-green">ACHIEVEMENTS</div></div>',
            unsafe_allow_html=True,
        )
        for badge in badges:
            st.caption(badge)

    st.markdown("---")

    if st.button(
        "🔊 SOUND ON" if st.session_state.sound_on else "🔇 SOUND OFF",
        use_container_width=True,
    ):
        toggle_sound()
        st.rerun()

    st.markdown(
        """
        <div class="manifesto">
            <div class="manifesto-line">WE ARE <span>VENOM</span>.</div>
            <div class="manifesto-line">WE DON'T <span>LOSE</span>.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="dev-credit">Built by <span>@Ahadu</span> • © 2026</div>',
        unsafe_allow_html=True,
    )


# ============================================================
# NON-GAME PAGES
# ============================================================

if st.session_state.page == "category":

    render_header("HANGMAN", "CATEGORY SELECTOR")

    st.subheader("Choose a category")

    cat_list = list(WORD_BANK.keys())

    for row_start in range(0, len(cat_list), 2):
        row_categories = cat_list[row_start:row_start + 2]
        cols = st.columns(len(row_categories))

        for col, category in zip(cols, row_categories):
            with col:
                with st.container(key=f"cat_card_{category}"):
                    icon = CATEGORY_ICONS.get(category, "🔤")
                    is_active = category == st.session_state.category

                    if st.button(
                        f"{icon}  {category.upper()}",
                        use_container_width=True,
                        key=f"catbtn_{category}",
                    ):
                        start_new_game(category=category)
                        st.session_state.page = "game"
                        st.rerun()

                    if is_active:
                        st.markdown(
                            '<div class="card-tag active">★ CURRENT</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<div class="card-tag">{len(WORD_BANK[category])} WORDS</div>',
                            unsafe_allow_html=True,
                        )

    st.stop()


if st.session_state.page == "stats":

    render_header("STATS", "YOUR HANGMAN RECORD")

    a, b, c = st.columns(3)

    with a:
        st.metric("Games Played", st.session_state.games_played)

    with b:
        st.metric("Games Won", st.session_state.games_won)

    with c:
        win_rate = (
            round(st.session_state.games_won * 100 / st.session_state.games_played)
            if st.session_state.games_played
            else 0
        )
        st.metric("Win Rate", f"{win_rate}%")

    st.metric("Current Streak", st.session_state.current_streak)
    st.metric("Best Streak", st.session_state.best_streak)

    badges = get_achievements()
    if badges:
        st.markdown("**Achievements**")
        for badge in badges:
            st.write(badge)
    else:
        st.caption("No achievements unlocked yet — win a few rounds to start earning badges.")

    if st.button("← BACK TO GAME", use_container_width=True):
        st.session_state.page = "game"
        st.rerun()

    st.stop()


if st.session_state.page == "settings":

    render_header("SETTINGS", "GAME PREFERENCES")

    st.selectbox(
        "DIFFICULTY",
        list(DIFFICULTY_ATTEMPTS.keys()),
        key="difficulty",
        on_change=on_difficulty_change,
    )
    st.caption(f"{DIFFICULTY_ATTEMPTS[st.session_state.difficulty]} attempts allowed before Venom wins.")

    st.write(f"Sound: {'On' if st.session_state.sound_on else 'Off'}")

    if st.button("← BACK TO GAME", use_container_width=True):
        st.session_state.page = "game"
        st.rerun()

    st.stop()


# ============================================================
# GAME PAGE
# ============================================================

render_header("HANGMAN", "DECODE THE WORD • SURVIVE THE VENOM")


# ---------------- TOP CONTROLS ----------------

cat_col, diff_col = st.columns(2)

categories = list(WORD_BANK.keys())
difficulties = list(DIFFICULTY_ATTEMPTS.keys())

with cat_col:
    st.selectbox(
        "CATEGORY",
        categories,
        key="category",
        on_change=on_category_change,
    )

with diff_col:
    st.selectbox(
        "DIFFICULTY",
        difficulties,
        key="difficulty",
        on_change=on_difficulty_change,
    )

st.markdown(
    '<div class="panel-title">GUESS THE WORD</div>',
    unsafe_allow_html=True,
)


# ---------------- ARENA HUD (background band) ----------------

evaluate_game()
render_sound()  # plays the correct/wrong/win/lose tone for the action that just happened, if any
render_fireworks()  # bursts once on a win, self-clears on the next rerun

hearts_html = "".join(
    '<span class="heart-full">❤</span>' for _ in range(st.session_state.attempts_left)
) + "".join(
    '<span class="heart-empty">♥</span>'
    for _ in range(st.session_state.max_attempts - st.session_state.attempts_left)
)

with st.container(key="arena_hud"):

    st.markdown(
        f"""
        <div class="hud-row">
            <div class="hud-caption">
                {st.session_state.category.upper()} • {len(st.session_state.word)} LETTERS
            </div>
            <div class="tries-label">TRIES LEFT: <span class="hearts">{hearts_html}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="word_row"):
        word_cols = st.columns(len(st.session_state.word))

        for i, letter in enumerate(st.session_state.word):
            with word_cols[i]:
                visible = (
                    letter.upper()
                    if letter in st.session_state.used_letters
                    else "_"
                )
                st.button(
                    visible,
                    key=f"word_display_{i}",
                    disabled=True,
                    use_container_width=True,
                )


# ---------------- RESULT ----------------
# Same fix as render_sound() above: this banner only exists 0 or
# 1 times depending on game state, sitting right before the
# keyboard — an identical trigger for the same duplication bug.
# A stable placeholder keeps its position constant either way.

result_slot = st.empty()

if st.session_state.gave_up:
    result_slot.error(f"☠ YOU GAVE UP — THE WORD WAS {st.session_state.word.upper()}")
elif st.session_state.won:
    result_slot.success(f"☠ VENOM DEFEATED — {st.session_state.word.upper()}")
elif st.session_state.game_over:
    result_slot.error(f"💀 GAME OVER — THE WORD WAS {st.session_state.word.upper()}")
else:
    result_slot.empty()


# ---------------- KEYBOARD ----------------

render_keyboard_bridge()  # lets a physical keyboard drive the same buttons below

with st.container(key="kbd_panel"):

    for row_index, row_letters in enumerate(KEYBOARD_ROWS):

        with st.container(key=f"kbd_row_{row_index}"):
            cols = st.columns(len(row_letters))

            for offset, letter in enumerate(row_letters):
                with cols[offset]:

                    # Stable button key regardless of state — only the
                    # label/type/disabled props change. This is what
                    # fixes the duplicate/"ghost" keyboard rows: changing
                    # a widget's key between reruns makes Streamlit treat
                    # it as a brand-new widget instead of updating the
                    # existing one. No per-letter container needed either
                    # — the CSS below is already scoped to kbd_panel as a
                    # whole, so one less moving part to go wrong.

                    already_used = letter in st.session_state.used_letters

                    if already_used:
                        is_correct = letter in st.session_state.word
                        btn_type = "primary" if is_correct else "tertiary"
                    else:
                        btn_type = "secondary"

                    if st.button(
                        letter.upper(),
                        key=f"key_{letter}",
                        type=btn_type,
                        disabled=already_used or st.session_state.game_over,
                        use_container_width=True,
                    ):
                        guess_letter(letter)
                        st.rerun()


# ---------------- BOTTOM ACTIONS ----------------

action_hint, action_giveup = st.columns(2)

with action_hint:
    with st.container(key="btn_hint"):
        if st.button(
            f"💡 HINT ({st.session_state.hints_left})",
            disabled=(
                st.session_state.game_over
                or st.session_state.hints_left <= 0
            ),
            use_container_width=True,
        ):
            use_hint()
            st.rerun()

with action_giveup:
    with st.container(key="btn_giveup"):
        if st.button(
            "☠ GIVE UP",
            disabled=st.session_state.game_over,
            use_container_width=True,
        ):
            give_up()
            st.rerun()


# ---------------- PLAY AGAIN ----------------

play_again_slot = st.empty()

if st.session_state.game_over:
    with play_again_slot.container():
        st.write("")
        with st.container(key="btn_play_again"):
            if st.button("🎮 PLAY AGAIN", use_container_width=True):
                start_new_game()
                st.rerun()
else:
    play_again_slot.empty()