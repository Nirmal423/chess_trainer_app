import streamlit as st
import chess
import chess.pgn
import chess.engine
import chess.svg
import streamlit.components.v1 as components
import requests
import io

st.set_page_config(page_title="Chess Trainer", layout="wide", page_icon="♟️")

# ---------- Chess.com / Lichess inspired theme ----------
st.markdown("""
<style>
:root {
  --cc-green: #81b64c;
  --cc-green-dark: #6ba33d;
  --cc-bg: #262421;
  --cc-panel: #2e2c29;
  --cc-text: #e9e9e6;
  --cc-muted: #b5b3ae;
}
.stApp { background-color: var(--cc-bg); color: var(--cc-text); }
section[data-testid="stSidebar"] { background-color: var(--cc-panel); }
h1, h2, h3 { color: var(--cc-green) !important; font-family: 'Segoe UI', sans-serif; }
div.stButton > button {
  background-color: var(--cc-green); color: white; border: none;
  border-radius: 6px; font-weight: 600; padding: 6px 14px;
}
div.stButton > button:hover { background-color: var(--cc-green-dark); color: white; }

/* Fix metric contrast */
div[data-testid="stMetric"] {
  background-color: var(--cc-panel); border-radius: 10px; padding: 12px;
  border: 1px solid #3d3a36;
}
div[data-testid="stMetric"] * {
  color: #f0efe9 !important;
  -webkit-text-fill-color: #f0efe9 !important;
  opacity: 1 !important;
}
div[data-testid="stMetricValue"] { font-size: 1.8em !important; font-weight: 700 !important; }
div[data-testid="stMetricLabel"] { font-weight: 700 !important; }

.move-label {
  font-size: 1.1em; font-weight: 600; padding: 8px 14px; border-radius: 6px;
  display: inline-block; margin-bottom: 8px;
}
.lbl-best, .lbl-good { background-color: #3a5f2a; color: #b8e6a0; }
.lbl-inaccuracy { background-color: #6b5a1e; color: #f0d878; }
.lbl-mistake { background-color: #7a4a1e; color: #f0a878; }
.lbl-blunder { background-color: #7a2020; color: #f5a0a0; }

.move-row-num { color: var(--cc-muted); font-size: 0.9em; padding-top: 6px; }

/* Fix low-contrast sidebar labels */
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] label p,
section[data-testid="stSidebar"] .stSlider label,
section[data-testid="stSidebar"] .stTextInput label,
section[data-testid="stSidebar"] .stNumberInput label {
  color: var(--cc-text) !important;
  font-weight: 600 !important;
  opacity: 1 !important;
}
</style>
""", unsafe_allow_html=True)

st.title("♟️ Chess Improvement Trainer")

# ---------- Sidebar config ----------
st.sidebar.header("Setup")
import shutil as _shutil
_auto_stockfish = _shutil.which("stockfish") or _shutil.which("stockfish.exe") or ""
_default_stockfish = _auto_stockfish or r"C:\Users\Havisha Nirmal\Downloads\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe"
stockfish_path = st.sidebar.text_input(
    "Stockfish path",
    value=_default_stockfish,
    help="Auto-detected when deployed on Streamlit Cloud. Locally, this is the full path to your stockfish .exe"
)
target_elo = st.sidebar.number_input("Target rapid ELO", value=1200, step=50)
analysis_depth = st.sidebar.slider("Analysis depth", 8, 20, 14)
board_size = st.sidebar.slider("Board size", 320, 560, 440, step=20)
gemini_api_key = st.sidebar.text_input("Gemini API key (optional)", type="password",
                                        help="From aistudio.google.com — free, no card required. "
                                             "Leave blank to use the built-in free rule-based explanations instead.")

# ---------- Classification ----------
def classify(cp_loss):
    if cp_loss <= 10:
        return "Best", "🟢", "lbl-best"
    elif cp_loss <= 25:
        return "Good", "🟢", "lbl-good"
    elif cp_loss <= 50:
        return "Inaccuracy", "🟡", "lbl-inaccuracy"
    elif cp_loss <= 100:
        return "Mistake", "🟠", "lbl-mistake"
    else:
        return "Blunder", "🔴", "lbl-blunder"

# ---------- chess.com fetch ----------
@st.cache_data(show_spinner=False)
def get_archives(username):
    url = f"https://api.chess.com/pub/player/{username}/games/archives"
    r = requests.get(url, headers={"User-Agent": "chess-trainer-app"})
    r.raise_for_status()
    return r.json()["archives"]

@st.cache_data(show_spinner=False)
def get_games(archive_url):
    r = requests.get(archive_url, headers={"User-Agent": "chess-trainer-app"})
    r.raise_for_status()
    return r.json()["games"]

def eval_and_best(board, engine, depth):
    info = engine.analyse(board, chess.engine.Limit(depth=depth))
    score = info["score"].white().score(mate_score=100000)
    pv = info.get("pv", [])
    return score, (pv[0] if pv else None)

PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}
PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}

def _piece_name(piece_type):
    return PIECE_NAMES.get(piece_type, "piece")

def explain_move_rule_based(fen_before, played_san, best_san, cp_loss, label):
    """Free, rule-based explanation using python-chess tactical pattern
    detection — no LLM or API key required."""
    board = chess.Board(fen_before)
    mover_color = board.turn
    opponent_word = "Black" if mover_color == chess.WHITE else "White"

    try:
        played_move = board.parse_san(played_san)
    except Exception:
        played_move = None
    try:
        best_move = board.parse_san(best_san)
    except Exception:
        best_move = None

    # 1. Missed forced checkmate
    if best_move:
        b_best = board.copy()
        b_best.push(best_move)
        if b_best.is_checkmate():
            return (f"This missed a forced checkmate — **{best_san}** would have "
                     f"ended the game immediately.")

    if played_move:
        board_after = board.copy()
        board_after.push(played_move)

        # 2. Played move allows an immediate mate
        for resp in board_after.legal_moves:
            b3 = board_after.copy()
            b3.push(resp)
            if b3.is_checkmate():
                resp_san = board_after.san(resp)
                return (f"This allows **{resp_san}**, which is checkmate. "
                         f"The engine's move **{best_san}** would have avoided this.")

        # 3. The piece that moved is left hanging (attacked, under-defended)
        moved_piece = board.piece_at(played_move.from_square)
        dest = played_move.to_square
        if moved_piece:
            attackers = board_after.attackers(not mover_color, dest)
            defenders = board_after.attackers(mover_color, dest)
            if attackers:
                piece_val = PIECE_VALUES.get(moved_piece.piece_type, 0)
                weakest_attacker_sq = min(
                    attackers,
                    key=lambda sq: PIECE_VALUES.get(board_after.piece_at(sq).piece_type, 1)
                )
                weakest_attacker_val = PIECE_VALUES.get(
                    board_after.piece_at(weakest_attacker_sq).piece_type, 1
                )
                if not defenders or weakest_attacker_val < piece_val:
                    attacker_name = _piece_name(board_after.piece_at(weakest_attacker_sq).piece_type)
                    return (f"This leaves your {_piece_name(moved_piece.piece_type)} on "
                             f"{chess.square_name(dest)} hanging — {opponent_word} can capture it "
                             f"with a {attacker_name} for free. The engine preferred **{best_san}** instead.")

        # 4. Missed a free capture that the engine's move takes
        if best_move and board.is_capture(best_move) and not board.is_capture(played_move):
            captured_piece = board.piece_at(best_move.to_square)
            if captured_piece:
                defenders_of_target = board.attackers(not mover_color, best_move.to_square)
                if len(defenders_of_target) == 0:
                    return (f"You missed winning material — **{best_san}** captures an "
                             f"undefended {_piece_name(captured_piece.piece_type)}.")

    # 5. Fallback: no clean single-pattern match, describe in evaluation terms
    pawns_lost = round(cp_loss / 100, 1)
    return (f"This move gives up roughly {pawns_lost} pawns of advantage compared to the "
             f"engine's top choice, **{best_san}** — a **{label}**, though it doesn't fit a "
             f"single simple tactical pattern (worth a closer look on the board).")

def explain_move_gemini(fen_before, played_san, best_san, cp_loss, label, api_key):
    """Plain-English explanation via Google Gemini (free tier). Raises on
    any failure so the caller can fall back to the rule-based explainer."""
    from google import genai
    client = genai.Client(api_key=api_key)
    prompt = (
        f"Position (FEN): {fen_before}\nMove played: {played_san}\n"
        f"Engine's best move: {best_san}\nCentipawn loss: {cp_loss} ({label})\n\n"
        "In 1-2 plain English sentences, explain to a ~1000 ELO rapid player "
        "why the played move was worse than the engine's best move. Be concrete."
    )
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Empty response from Gemini")
    return text

def explain_move(fen_before, played_san, best_san, cp_loss, label, gemini_api_key=None):
    """Tries Gemini first (if a key was provided); falls back to the free
    rule-based explainer on any error (bad key, quota, network, etc.)."""
    if gemini_api_key:
        try:
            return explain_move_gemini(fen_before, played_san, best_san, cp_loss, label, gemini_api_key)
        except Exception as e:
            fallback = explain_move_rule_based(fen_before, played_san, best_san, cp_loss, label)
            return f"{fallback}\n\n_(Gemini explanation unavailable: {e})_"
    return explain_move_rule_based(fen_before, played_san, best_san, cp_loss, label)

DRILLS = {
    "opening": ["Lichess opening explorer: https://lichess.org/opening",
                "Chess.com opening lessons: https://www.chess.com/lessons"],
    "middlegame": ["Hanging pieces: https://lichess.org/training/hangingPiece",
                   "Forks: https://lichess.org/training/fork",
                   "Pins: https://lichess.org/training/pin"],
    "endgame": ["Basic checkmates: https://lichess.org/practice/basic-endgames-i/basic-checkmates-1",
                "Pawn endgames: https://lichess.org/practice/pawn-endgames"]
}

def phase(move_number):
    if move_number <= 10:
        return "opening"
    elif move_number <= 30:
        return "middlegame"
    return "endgame"

def render_board(fen, lastmove=None, flipped=False, size=440):
    board = chess.Board(fen)
    svg = chess.svg.board(
        board=board, lastmove=lastmove, size=size, coordinates=True,
        orientation=(chess.BLACK if flipped else chess.WHITE),
        colors={"square light": "#eeeed2", "square dark": "#769656",
                "square light lastmove": "#f7f769", "square dark lastmove": "#bbcb44"}
    )
    components.html(svg, height=size + 20)

# ---------- Navigation callbacks ----------
def go_to(ply):
    st.session_state.ply_index = ply

def go_prev():
    st.session_state.ply_index = max(0, st.session_state.ply_index - 1)

def go_next():
    max_ply = len(st.session_state.analysis["positions"]) - 1
    st.session_state.ply_index = min(max_ply, st.session_state.ply_index + 1)

def go_start():
    st.session_state.ply_index = 0

def go_end():
    st.session_state.ply_index = len(st.session_state.analysis["positions"]) - 1

# ---------- Main flow ----------
username = st.text_input("chess.com username")

if username:
    try:
        archives = get_archives(username)
    except Exception as e:
        st.error(f"Couldn't fetch games for '{username}': {e}")
        st.stop()

    month_labels = [a.split("/")[-2] + "-" + a.split("/")[-1] for a in archives]
    chosen_month = st.selectbox("Month", options=list(reversed(month_labels)))
    chosen_archive = archives[month_labels.index(chosen_month)]

    games = get_games(chosen_archive)
    if not games:
        st.warning("No games found for that month.")
        st.stop()

    game_labels = []
    for g in games:
        white = g.get("white", {}).get("username", "?")
        black = g.get("black", {}).get("username", "?")
        result = g.get("white", {}).get("result", "?")
        game_labels.append(f"{white} vs {black} ({g.get('time_class','?')}, result: {result})")

    chosen_idx = st.selectbox("Game", options=range(len(games)), format_func=lambda i: game_labels[i])
    selected_game = games[chosen_idx]

    if st.button("Analyze this game"):
        pgn_io = io.StringIO(selected_game["pgn"])
        game = chess.pgn.read_game(pgn_io)
        board = game.board()

        user_is_white = selected_game.get("white", {}).get("username", "").lower() == username.lower()
        user_color = chess.WHITE if user_is_white else chess.BLACK

        with st.spinner("Running Stockfish analysis..."):
            try:
                engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
            except Exception as e:
                st.error(f"Couldn't start Stockfish at that path: {e}")
                st.stop()

            positions = [{"ply": 0, "fen": board.fen(), "move": None, "san": None,
                          "mover": None, "label": None, "icon": None, "cls": None}]
            results = []
            phase_stats = {"opening": [], "middlegame": [], "endgame": []}

            current_eval, current_best = eval_and_best(board, engine, analysis_depth)
            move_number = 0

            for move in game.mainline_moves():
                move_number += 1
                mover_is_user = (board.turn == user_color)
                fen_before = board.fen()
                eval_before, best_move = current_eval, current_best
                played_san = board.san(move)
                best_san = board.san(best_move) if best_move else played_san

                board.push(move)
                eval_after, next_best = eval_and_best(board, engine, analysis_depth)

                if board.turn == chess.BLACK:  # white just moved
                    cp_loss = max(0, eval_before - eval_after)
                else:
                    cp_loss = max(0, eval_after - eval_before)

                label, icon, cls = classify(cp_loss)
                ph = phase(move_number)

                entry = {
                    "ply": move_number, "fen": board.fen(), "move": move, "san": played_san,
                    "mover": "you" if mover_is_user else "opponent",
                    "cp_loss": cp_loss, "label": label, "icon": icon, "cls": cls,
                    "best_san": best_san, "fen_before": fen_before, "phase": ph
                }
                positions.append(entry)

                if mover_is_user:
                    phase_stats[ph].append(cp_loss)
                    if label in ("Inaccuracy", "Mistake", "Blunder"):
                        results.append(entry)

                current_eval, current_best = eval_after, next_best

            engine.quit()

        st.session_state.analysis = {
            "positions": positions, "results": results, "phase_stats": phase_stats,
            "user_color": user_color, "flipped": (user_color == chess.BLACK)
        }
        st.session_state.ply_index = 0
        st.session_state.explanations = {}

# ---------- Display (persists across nav clicks) ----------
if "analysis" in st.session_state:
    data = st.session_state.analysis
    positions = data["positions"]
    results = data["results"]
    phase_stats = data["phase_stats"]

    st.header("Summary")
    counts = {}
    user_moves = [p for p in positions if p["mover"] == "you"]
    for r in user_moves:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    total = len(user_moves)
    accuracy = 100 * sum(1 for r in user_moves if r["label"] in ("Best", "Good")) / total if total else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Move accuracy", f"{accuracy:.0f}%")
    c2.metric("Blunders", counts.get("Blunder", 0))
    c3.metric("Mistakes", counts.get("Mistake", 0))
    c4.metric("Inaccuracies", counts.get("Inaccuracy", 0))

    weak_phase = max(phase_stats, key=lambda p: sum(phase_stats[p]) if phase_stats[p] else 0)
    st.write(f"**Weakest phase: {weak_phase.title()}** — suggested drills:")
    for d in DRILLS[weak_phase]:
        st.write(f"- {d}")

    st.divider()
    st.header("Replay board")

    flipped = st.checkbox("Flip board", value=data["flipped"])
    current = positions[st.session_state.ply_index]

    board_col, list_col = st.columns([3, 2])

    with board_col:
        render_board(current["fen"], lastmove=current["move"], flipped=flipped, size=board_size)
        nc1, nc2, nc3, nc4, nc5 = st.columns([1, 1, 3, 1, 1])
        nc1.button("⏮", on_click=go_start, use_container_width=True)
        nc2.button("◀", on_click=go_prev, use_container_width=True)
        nc3.slider("Move", 0, len(positions) - 1, key="ply_index", label_visibility="collapsed")
        nc4.button("▶", on_click=go_next, use_container_width=True)
        nc5.button("⏭", on_click=go_end, use_container_width=True)

        if current["ply"] == 0:
            st.write("**Starting position**")
        else:
            st.write(f"**Move {current['ply']}: {current['san']}**")
            st.caption("Your move" if current["mover"] == "you" else "Opponent's move")
            if current["label"]:
                st.markdown(f'<span class="move-label {current["cls"]}">{current["icon"]} {current["label"]}</span>',
                            unsafe_allow_html=True)
            if current["mover"] == "you" and current["label"] in ("Inaccuracy", "Mistake", "Blunder"):
                st.write(f"Engine preferred: **{current['best_san']}**")
                cache = st.session_state.explanations
                if current["ply"] not in cache:
                    with st.spinner("Getting explanation..."):
                        cache[current["ply"]] = explain_move(
                            current["fen_before"], current["san"], current["best_san"],
                            current["cp_loss"], current["label"], gemini_api_key
                        )
                st.info(cache[current["ply"]])

    with list_col:
        st.markdown("**Moves**")
        with st.container(height=board_size + 40):
            # Build white/black pairs like a PGN panel
            ply = 1
            move_no = 1
            while ply < len(positions):
                white_entry = positions[ply] if ply < len(positions) else None
                black_entry = positions[ply + 1] if ply + 1 < len(positions) else None

                rc = st.columns([1, 3, 3])
                rc[0].markdown(f'<div class="move-row-num">{move_no}.</div>', unsafe_allow_html=True)

                if white_entry:
                    icon = white_entry["icon"] or ""
                    is_current = (st.session_state.ply_index == white_entry["ply"])
                    label = f"{'▶ ' if is_current else ''}{icon} {white_entry['san']}"
                    rc[1].button(label, key=f"mv_{white_entry['ply']}",
                                 on_click=go_to, args=(white_entry["ply"],),
                                 use_container_width=True)

                if black_entry:
                    icon = black_entry["icon"] or ""
                    is_current = (st.session_state.ply_index == black_entry["ply"])
                    label = f"{'▶ ' if is_current else ''}{icon} {black_entry['san']}"
                    rc[2].button(label, key=f"mv_{black_entry['ply']}",
                                 on_click=go_to, args=(black_entry["ply"],),
                                 use_container_width=True)

                ply += 2
                move_no += 1

    st.divider()
    st.header("Flagged moves — click to jump")
    for r in results:
        cols = st.columns([5, 1])
        cols[0].markdown(
            f'<span class="move-label {r["cls"]}">{r["icon"]} Move {r["ply"]}: {r["san"]} — {r["label"]}</span>',
            unsafe_allow_html=True
        )
        cols[1].button("View", key=f"jump_{r['ply']}", on_click=go_to, args=(r["ply"],), use_container_width=True)