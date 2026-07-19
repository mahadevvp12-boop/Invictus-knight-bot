import threading
import json
import requests
import os
import random
import time
import traceback
import atexit
import subprocess
import platform
import stat
import chess
import chess.variant
import chess.engine

# --- 1. GLOBAL CONFIGURATION & ENVIRONMENT VARIABLES ---
TOKEN = os.environ.get("LICHESS_TOKEN", "YOUR_SECRET_TOKEN_HERE")
BOT_USERNAME = os.environ.get("LICHESS_USERNAME", "Invictus-knight-bot")

STOCKFISH_PATH = "./stockfish"
FAIRY_STOCKFISH_PATH = "./fairy-stockfish"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
}

# Variant option name discovered at engine init time (if available)
VARIANT_OPTION_NAME = None

# --- 2. ENGINE DOWNLOADER (RUN BEFORE ENGINE INIT) ---
def _download_file(url, dest_path, chunk_size=8192):
    """Download a file with requests and make it executable."""
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
        # make executable
        os.chmod(dest_path, os.stat(dest_path).st_mode | stat.S_IEXEC)
        return True
    except Exception as e:
        print(f"[DOWNLOAD ERROR] Failed to download {url}: {e}")
        return False


def download_engines_if_missing():
    """Guarantees both chess binaries exist and are executable. Uses requests-based downloader.
    Returns True if both engines are present (or were successfully downloaded), False otherwise.
    """
    # Basic platform check
    system = platform.system()
    machine = platform.machine().lower()
    if system != "Linux":
        print(f"[SETUP WARNING] Expected Linux platform for prebuilt binaries, got: {system}.\n" \
              "If you're running on another OS, provide compatible engine binaries in the repo.")
        # Continue — user may have provided binaries manually.

    # 1. Standard Stockfish 18
    if not os.path.exists(STOCKFISH_PATH):
        print("[SETUP] stockfish binary not found; attempting to download...")
        stockfish_url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-avx2"
        if not _download_file(stockfish_url, STOCKFISH_PATH):
            print("[SETUP ERROR] Standard Stockfish download failed.")
            return False
        print("[SETUP] Standard Stockfish ready.")

    # 2. Fairy Stockfish (for variants)
    if not os.path.exists(FAIRY_STOCKFISH_PATH):
        print("[SETUP] fairy-stockfish binary not found; attempting to download...")
        fairy_url = "https://github.com/fairy-stockfish/Fairy-Stockfish/releases/download/fairy_sf_14.1/fairy-stockfish-large-linux-x86-64"
        if not _download_file(fairy_url, FAIRY_STOCKFISH_PATH):
            print("[SETUP ERROR] Fairy Stockfish download failed.")
            return False
        print("[SETUP] Fairy Stockfish ready.")

    return True

# Run downloader before engine initialization
if not download_engines_if_missing():
    print("[CRITICAL] Engine setup failed. Exiting.")
    exit(1)

# --- 3. GLOBAL STATE & CONFIGURATION ---
engine_standard = None
engine_variants = None

VARIANT_MAP = {
    'standard': chess.Board,
    'atomic': chess.variant.AtomicBoard,
    'crazyhouse': chess.variant.CrazyhouseBoard,
    'antichess': chess.variant.AntichessBoard,
    'horde': chess.variant.HordeBoard,
    'kingOfTheHill': chess.variant.KingOfTheHillBoard,
    'racingKings': chess.variant.RacingKingsBoard,
    'threeCheck': chess.variant.ThreeCheckBoard
}

UCI_VARIANT_MAP = {
    'atomic': 'atomic',
    'crazyhouse': 'crazyhouse',
    'antichess': 'antichess',
    'horde': 'horde',
    'kingOfTheHill': 'kingofthehill',
    'racingKings': 'racingkings',
    'threeCheck': '3check'
}

lock_standard = threading.Lock()
lock_variants = threading.Lock()

# --- 4. ENGINE INITIALIZATION & CLEANUP ---
def init_engines():
    """Initialize chess engines with some defensive checks and discover engine options."""
    global engine_standard, engine_variants, VARIANT_OPTION_NAME
    try:
        print("[INIT] Starting Stockfish processes...")
        engine_standard = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine_variants = chess.engine.SimpleEngine.popen_uci(FAIRY_STOCKFISH_PATH)

        # Inspect engine options to find a likely variant option name (if any)
        try:
            # engine.options is usually a dict-like mapping
            opts = engine_variants.options
            for opt_name in opts:
                if 'variant' in opt_name.lower() or 'uci_variant' in opt_name.lower():
                    VARIANT_OPTION_NAME = opt_name
                    break
            if VARIANT_OPTION_NAME:
                print(f"[INIT] Detected engine variant option name: {VARIANT_OPTION_NAME}")
            else:
                print("[INIT] No variant option name detected on engine; will attempt known key if needed.")
        except Exception:
            print("[INIT] Could not inspect engine options for variant support.")

        print("[SUCCESS] Both engines initialized.")
    except Exception as init_err:
        print(f"[CRITICAL] Failed to load engine binaries. Error: {init_err}")
        engine_standard = None
        engine_variants = None


def cleanup_engines():
    """Cleanup: Close background engine processes on shutdown."""
    print("[SHUTDOWN] Closing background engine processes...")
    if engine_standard:
        try:
            engine_standard.quit()
        except Exception:
            pass
    if engine_variants:
        try:
            engine_variants.quit()
        except Exception:
            pass

init_engines()
atexit.register(cleanup_engines)

# --- 5. LICHESS API HELPER FUNCTIONS ---
def send_chat_message(game_id, room, text):
    """Sends a chat message to the opponent or spectator room."""
    url = f"https://lichess.org/api/bot/game/{game_id}/chat"
    data = {"room": room, "text": text}
    try:
        requests.post(url, headers={**HEADERS, "Content-Type": "application/json"}, json=data, timeout=10)
    except Exception as e:
        print(f"[{game_id}] Failed to send chat: {e}")


def make_lichess_move(game_id, move_str):
    """Sends the calculated move back to Lichess."""
    url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_str}"
    try:
        response = requests.post(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            print(f"[{game_id}] Played move: {move_str}")
        else:
            print(f"[{game_id}] Move failed ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[{game_id}] Error posting move: {e}")

# --- 6. TACTICAL ENGINE CALCULATION ---
def get_engine_move(moves_list, variant_key='standard'):
    """Calculates tactical moves safely across multiple simultaneous game threads."""
    board_class = VARIANT_MAP.get(variant_key, chess.Board)
    board = board_class()

    for move in moves_list:
        try:
            board.push_uci(move)
        except Exception:
            pass

    if board.is_game_over() or not list(board.legal_moves):
        return None

    is_standard = (variant_key == 'standard')
    engine = engine_standard if is_standard else engine_variants
    lock = lock_standard if is_standard else lock_variants

    if engine is None:
        print("[WARNING] Engines uninitialized, playing random move.")
        return random.choice(list(board.legal_moves)).uci()

    with lock:
        try:
            if not is_standard:
                # Prefer discovered option name but fall back to known key
                uci_variant_name = UCI_VARIANT_MAP.get(variant_key)
                if VARIANT_OPTION_NAME and uci_variant_name:
                    try:
                        engine.configure({VARIANT_OPTION_NAME: uci_variant_name})
                    except Exception as e:
                        print(f"[ENGINE CONFIG] Failed to set {VARIANT_OPTION_NAME}: {e}")
                        # attempt fallback
                        try:
                            engine.configure({"UCI_Variant": uci_variant_name})
                        except Exception:
                            pass
                elif uci_variant_name:
                    try:
                        engine.configure({"UCI_Variant": uci_variant_name})
                    except Exception as e:
                        print(f"[ENGINE CONFIG] Failed to set UCI_Variant: {e}")

            # Keep thinking time small but safe; adjust as desired
            result = engine.play(board, chess.engine.Limit(time=0.1))
            if result.move:
                return result.move.uci()

        except Exception as e:
            print(f"[ENGINE ERROR] Dynamic calculation failed for {variant_key}: {e}")
            traceback.print_exc()

    return random.choice(list(board.legal_moves)).uci()

# --- 7. INDIVIDUAL MATCH STREAMING LOOP ---
def play_game(game_id, variant_key='standard'):
    """Streams individual match events. Passes variant key down to the engine."""
    print(f"\n[GAME START] Thread spawned for game: {game_id} ({variant_key})")
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"

    try:
        response = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    except Exception as e:
        print(f"[{game_id}] Stream connection failed: {e}")
        return

    if getattr(response, 'status_code', None) != 200:
        print(f"[{game_id}] Game stream failed ({response.status_code}): {response.text}")
        return

    bot_color = None
    sent_welcome = False
    state = {}

    try:
        for line in response.iter_lines():
            if not line:
                continue

            try:
                game_event = json.loads(line.decode('utf-8'))
            except Exception:
                continue

            if game_event.get('type') == 'gameState' and game_event.get('status') != 'started':
                print(f"[{game_id}] Match complete. Reason: {game_event.get('status')}")
                send_chat_message(game_id, "player", "Good game! Thanks for playing.")
                break

            if game_event.get('type') == 'gameFull':
                white_id = game_event['white'].get('id', '').lower()
                bot_color = 'white' if white_id == BOT_USERNAME.lower() else 'black'
                state = game_event['state']

                if state.get('status') != 'started':
                    break

                if not sent_welcome:
                    welcome_msg = f"Hello! I am playing {variant_key} chess. Good luck!"
                    send_chat_message(game_id, "player", welcome_msg)
                    sent_welcome = True

            elif game_event.get('type') == 'gameState':
                state = game_event
            else:
                continue

            moves_played = state.get('moves', '').strip().split() if state.get('moves', '').strip() else []
            total_moves = len(moves_played)

            is_bot_turn = (total_moves % 2 == 0 and bot_color == 'white') or \
                          (total_moves % 2 != 0 and bot_color == 'black')

            if is_bot_turn:
                time.sleep(random.uniform(0.6, 1.8))
                bot_move = get_engine_move(moves_played, variant_key)
                if bot_move:
                    make_lichess_move(game_id, bot_move)
    except Exception as e:
        print(f"[{game_id}] Error while streaming game events: {e}")
        traceback.print_exc()


def listen_to_events():
    """Listens to global challenges and game starts with reconnect/backoff logic."""
    print(f"Starting global event listener for user: {BOT_USERNAME}")
    url = "https://lichess.org/api/stream/event"

    backoff = 1
    max_backoff = 60

    while True:
        try:
            response = requests.get(url, headers=HEADERS, stream=True, timeout=60)
            if response.status_code != 200:
                print(f"[EVENT STREAM] Connection failed: {response.status_code} {response.text}")
                time.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)
                continue

            # reset backoff on successful connection
            backoff = 1

            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    event = json.loads(line.decode('utf-8'))
                except Exception:
                    continue

                if event.get('type') == 'challenge':
                    challenge_id = event['challenge']['id']
                    variant = event['challenge']['variant']['key']

                    if variant not in VARIANT_MAP:
                        print(f"[CHALLENGE] Declining unsupported variant '{variant}' for ID: {challenge_id}")
                        try:
                            requests.post(
                                f"https://lichess.org/api/bot/challenge/{challenge_id}/decline",
                                headers={**HEADERS, "Content-Type": "application/json"},
                                json={"reason": "variant"},
                                timeout=10
                            )
                        except Exception as e:
                            print(f"[CHALLENGE] Failed to decline challenge {challenge_id}: {e}")
                        continue

                    print(f"[CHALLENGE] Auto-accepting {variant} game. ID: {challenge_id}")
                    accept_url = f"https://lichess.org/api/bot/challenge/{challenge_id}/accept"
                    try:
                        requests.post(accept_url, headers=HEADERS, timeout=10)
                    except Exception as e:
                        print(f"[CHALLENGE] Failed to accept {challenge_id}: {e}")

                elif event.get('type') == 'gameStart':
                    game_id = event['game']['id']
                    game_variant = event['game'].get('variant', {}).get('key', 'standard')
                    game_thread = threading.Thread(target=play_game, args=(game_id, game_variant))
                    game_thread.daemon = True
                    game_thread.start()

        except Exception as e:
            print(f"[EVENT STREAM] Error: {e}")
            traceback.print_exc()
            time.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)

# --- 8. MAIN ENTRY POINT ---
if __name__ == "__main__":
    try:
        listen_to_events()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Received shutdown signal.")
        cleanup_engines()
    except Exception as e:
        print(f"[FATAL] {e}")
        traceback.print_exc()
        cleanup_engines()
