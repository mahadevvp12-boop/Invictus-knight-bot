import threading
import json
import requests
import os
import random
import time
import traceback
import atexit
import subprocess  # 🛠️ REQUIRED: Allows Python to execute system commands
import chess  
import chess.variant  
import chess.engine  

# --- 1. GLOBAL CONFIGURATION & ENVIRONMENT VARIABLES ---
TOKEN = os.environ.get("LICHESS_TOKEN", "YOUR_SECRET_TOKEN_HERE")
BOT_USERNAME = os.environ.get("LICHESS_USERNAME", "Invictus-knight-bot")

# Force paths to local folder names where the script will download them
STOCKFISH_PATH = "./stockfish"
FAIRY_STOCKFISH_PATH = "./fairy-stockfish"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# --- 2. SELF-HEALING RUNTIME DOWNLOADER ---
def download_engines_if_missing():
    """Guarantees both chess binaries exist and are fully executable on Railway."""
    # 1. Handle Standard Stockfish
    if not os.path.exists(STOCKFISH_PATH):
        print("[SETUP] Downloading verified Standard Stockfish...")
        try:
            # ✅ DIRECT LINK ATTACHED: Downloads the true Linux tarball archive
            subprocess.run("wget https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-ubuntu-x86-64-avx2.tar -O stockfish.tar", shell=True, check=True)
            subprocess.run("tar -xf stockfish.tar --strip-components=1", shell=True, check=True)
            subprocess.run("mv stockfish-ubuntu-x86-64-avx2 ./stockfish", shell=True, check=True)
            subprocess.run("rm -f stockfish.tar", shell=True, check=True)
        except Exception as e:
            print(f"[SETUP ERROR] Stockfish download failed: {e}")

    # 2. Handle Fairy Stockfish
    if not os.path.exists(FAIRY_STOCKFISH_PATH):
        print("[SETUP] Downloading verified Fairy Stockfish...")
        try:
            # ✅ DIRECT LINK ATTACHED: Downloads the true Linux variant zip file
            subprocess.run("wget https://github.com/fairy-stockfish/Fairy-Stockfish/releases/download/fairy_sf_14.1/fairy-stockfish-large-linux-x86-64.zip -O fairy.zip", shell=True, check=True)
            subprocess.run("unzip -o fairy.zip", shell=True, check=True)
            subprocess.run("mv fairy-stockfish-large-linux-x86-64 ./fairy-stockfish", shell=True, check=True)
            subprocess.run("rm -f fairy.zip", shell=True, check=True)
        except Exception as e:
            print(f"[SETUP ERROR] Fairy Stockfish download failed: {e}")

    # 3. Force execution rights
    try:
        subprocess.run("chmod +x ./stockfish ./fairy-stockfish", shell=True, check=True)
        print("[SETUP] Binary file verifications complete.")
    except Exception as e:
        print(f"[SETUP ERROR] Failed to assign executable rights: {e}")

# 🔥 RUN THE DOWNLOADER BEFORE OPENING ENGINE CONNECTIONS
download_engines_if_missing()

# --- 3. MAPS & CONCURRENCY LOCKS ---
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

# --- 4. PERSISTENT ENGINE INITIALIZATION ---
try:
    print("Initializing Stockfish processes...")
    engine_standard = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    engine_variants = chess.engine.SimpleEngine.popen_uci(FAIRY_STOCKFISH_PATH)
except Exception as init_err:
    print(f"CRITICAL: Failed to load engine binaries. Error: {init_err}")
    engine_standard = None
    engine_variants = None

def cleanup_engines():
    print("[SHUTDOWN] Closing background engine processes...")
    if engine_standard:
        try: engine_standard.quit()
        except: pass
    if engine_variants:
        try: engine_variants.quit()
        except: pass

atexit.register(cleanup_engines)


# --- 4. LICHESS API HELPER FUNCTIONS ---
def send_chat_message(game_id, room, text):
    """Sends a chat message to the opponent or spectator room."""
    url = f"https://lichess.org/api/bot/game/{game_id}/chat"
    data = {"room": room, "text": text}
    try:
        requests.post(url, headers=HEADERS, json=data)
    except Exception as e:
        print(f"[{game_id}] Failed to send chat: {e}")

def make_lichess_move(game_id, move_str):
    """Sends the calculated move back to Lichess."""
    url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_str}"
    try:
        response = requests.post(url, headers=HEADERS)
        if response.status_code == 200:
            print(f"[{game_id}] Played move: {move_str}")
        else:
            print(f"[{game_id}] Move failed ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[{game_id}] Error posting move: {e}")

# --- 5. TACTICAL ENGINE ENGINE CALCULATION ---
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
                uci_variant_name = UCI_VARIANT_MAP.get(variant_key)
                if uci_variant_name:
                    engine.configure({"UCI_Variant": uci_variant_name})
            
            result = engine.play(board, chess.engine.Limit(time=0.1))
            if result.move:
                return result.move.uci()
                
        except Exception as e:
            print(f"[ENGINE ERROR] Dynamic calculation failed for {variant_key}: {e}")
        
    return random.choice(list(board.legal_moves)).uci()

# --- 6. INDIVIDUAL MATCH STREAMING LOOP ---
def play_game(game_id, variant_key='standard'):
    """Streams individual match events. Passes variant key down to the engine."""
    print(f"\n[GAME START] Thread spawned for game: {game_id} ({variant_key})")
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"
    
    try:
        response = requests.get(url, headers=HEADERS, stream=True)
    except Exception as e:
        print(f"[{game_id}] Stream connection failed: {e}")
        return
        
    bot_color = None
    sent_welcome = False

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

        moves_played = state['moves'].strip().split() if state['moves'].strip() else []
        total_moves = len(moves_played)

        is_bot_turn = (total_moves % 2 == 0 and bot_color == 'white') or \
                      (total_moves % 2 != 0 and bot_color == 'black')

        if is_bot_turn:
            time.sleep(random.uniform(0.6, 1.8))
            bot_move = get_engine_move(moves_played, variant_key)
            if bot_move:
                make_lichess_move(game_id, bot_move)

def listen_to_events():
    """Listens to global challenges and game starts."""
    print(f"Starting global event listener for user: {BOT_USERNAME}")
    url = "https://lichess.org/api/stream/event"
    
    # ⏱️ Added a timeout parameter so the initial handshake doesn't hang indefinitely
    response = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    
    # 🚨 CRITICAL FIX: If Lichess returns a 401, 403, or 404, this forces a visible crash!
    if response.status_code != 200:
        raise Exception(f"Lichess Stream Connection Failed! HTTP Status Code: {response.status_code}. Response Content: {response.text}")
    
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
                requests.post(f"https://lichess.org/api/bot/challenge/{challenge_id}/decline", headers=HEADERS, json={"reason": "variant"})
                continue

            print(f"[CHALLENGE] Auto-accepting {variant} game. ID: {challenge_id}")
            accept_url = f"https://lichess.org/api/bot/challenge/{challenge_id}/accept"
            requests.post(accept_url, headers=HEADERS)

        elif event.get('type') == 'gameStart':
            game_id = event['game']['id']
            game_variant = event['game'].get('variant', {}).get('key', 'standard')
            game_thread = threading.Thread(target=play_game, args=(game_id, game_variant))
            game_thread.daemon = True
            game_thread.start()
