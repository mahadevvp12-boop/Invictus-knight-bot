import threading
import json
import requests
import os
import random
import time
import chess  
import chess.variant  # REQUIRED: Handles variant rules and legal moves

TOKEN = os.environ.get("LICHESS_TOKEN", "YOUR_SECRET_TOKEN_HERE")
# 🛠️ FIX: Reads the unique username dynamically from Railway
BOT_USERNAME = os.environ.get("LICHESS_USERNAME", "Invictus-knight-bot")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# 1. Map Lichess variant keys to python-chess board classes
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

# --- PIECE VALUES FOR EVALUATING VARIANT POSITIONS ---
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000
}

def evaluate_board(board):
    """Evaluates the material balance of the board dynamically."""
    if board.is_checkmate():
        return -99999 if board.turn else 99999
        
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            val = PIECE_VALUES.get(piece.piece_type, 0)
            if piece.color == chess.WHITE:
                score += val
            else:
                score -= val
    return score

def minimax(board, depth, alpha, beta, maximizing_player):
    """Looks ahead 'depth' number of moves to find the safest, strongest line."""
    if depth == 0 or board.is_game_over():
        return evaluate_board(board)

    legal_moves = list(board.legal_moves)
    
    if maximizing_player:
        max_eval = -float('inf')
        for move in legal_moves:
            board.push(move)
            evaluation = minimax(board, depth - 1, alpha, beta, False)
            board.pop()
            max_eval = max(max_eval, evaluation)
            alpha = max(alpha, evaluation)
            if beta <= alpha:
                break  # Prune branch
        return max_eval
    else:
        min_eval = float('inf')
        for move in legal_moves:
            board.push(move)
            evaluation = minimax(board, depth - 1, alpha, beta, True)
            board.pop()
            min_eval = min(min_eval, evaluation)
            beta = min(beta, evaluation)
            if beta <= alpha:
                break  # Prune branch
        return min_eval

def get_engine_move(moves_list, variant_key='standard'):
    """Calculates tactical moves using lookahead depth for all variants."""
    board_class = VARIANT_MAP.get(variant_key, chess.Board)
    board = board_class()
    
    for move in moves_list:
        try:
            board.push_uci(move)
        except Exception:
            pass
            
    if board.is_game_over():
        return None

    # Fallback to Cloud Eval database for standard chess speed
    if variant_key == 'standard':
        # ... (Keep your existing cloud-eval API request blocks here) ...
        pass

    # --- SMART ENGINE CALCULATION FOR VARIANTS ---
    legal_moves = list(board.legal_moves)
    if not legal_moves:
        return None

    best_move = random.choice(legal_moves) # Default fallback
    
    # Depth 2 looks 2 ply ahead (your move + opponent response). 
    # Do not set higher than 3 on free hosting or it will run out of time!
    depth = 2 
    
    if board.turn == chess.WHITE:
        best_value = -float('inf')
        for move in legal_moves:
            board.push(move)
            board_value = minimax(board, depth - 1, -float('inf'), float('inf'), False)
            board.pop()
            if board_value > best_value:
                best_value = board_value
                best_move = move
    else:
        best_value = float('inf')
        for move in legal_moves:
            board.push(move)
            board_value = minimax(board, depth - 1, -float('inf'), float('inf'), True)
            board.pop()
            if board_value < best_value:
                best_value = board_value
                best_move = move

    return best_move.uci()

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
            
            # Pass variant_key so the board generates the correct rules
            bot_move = get_engine_move(moves_played, variant_key)
            if bot_move:
                make_lichess_move(game_id, bot_move)

def listen_to_events():
    """Listens to global challenges and game starts."""
    print(f"Starting global event listener for user: {BOT_USERNAME}")
    url = "https://lichess.org/api/stream/event"
    
    response = requests.get(url, headers=HEADERS, stream=True)
    
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
                requests.post(f"https://lichess.org/api/challenge/{challenge_id}/decline", headers=HEADERS)
                continue

            print(f"[CHALLENGE] Auto-accepting {variant} game. ID: {challenge_id}")
            accept_url = f"https://lichess.org/api/challenge/{challenge_id}/accept"
            requests.post(accept_url, headers=HEADERS)

        elif event.get('type') == 'gameStart':
            game_id = event['game']['id']
            game_variant = event['game'].get('variant', {}).get('key', 'standard')
            game_thread = threading.Thread(target=play_game, args=(game_id, game_variant))
            game_thread.daemon = True
            game_thread.start()



import traceback

if __name__ == "__main__":
    while True:
        try:
            listen_to_events()
        except Exception as global_err:
            print(f"\n CRASH DETECTED: {global_err}")
            print("--- FULL ERROR TRACEBACK START ---")
            traceback.print_exc()
            print("--- FULL ERROR TRACEBACK END ---\n")
            print("Reconnecting in 10 seconds...")
            time.sleep(10)
          
