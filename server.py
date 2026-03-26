import eventlet
eventlet.monkey_patch()

from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# ==============================
# GLOBALS
# ==============================
MAX_PLAYERS = 100
players = {}
waiting_player = None
rooms = {}

# ==============================
# CONNECT
# ==============================
@socketio.on('connect')
def handle_connect():
    print(f"[+] Client connected: {request.sid}")

# ==============================
# JOIN GAME
# ==============================
@socketio.on('join')
def handle_join(data):
    global waiting_player

    username = f"Player{len(players) + 1}"
    players[request.sid] = username

    print(f"[+] {username} joined")

    # If no waiting player → wait
    if waiting_player is None:
        waiting_player = request.sid
        emit("message", f"[SERVER] Waiting for opponent...", to=request.sid)

    else:
        # Create room
        room = f"room_{waiting_player}_{request.sid}"

        join_room(room, sid=waiting_player)
        join_room(room, sid=request.sid)

        rooms[waiting_player] = room
        rooms[request.sid] = room

        emit("message", "[SERVER] 🎮 Game started!", room=room)

        print(f"[ROOM CREATED] {room}")

        waiting_player = None

# ==============================
# MESSAGE (GAME CHAT)
# ==============================
@socketio.on('message')
def handle_message(msg):
    if request.sid not in players:
        return

    username = players[request.sid]
    room = rooms.get(request.sid)

    print(f"[{username}] -> {msg}")

    if room:
        emit("message", f"[{username}]: {msg}", room=room)
    else:
        emit("message", "[SERVER] You are not in a game yet", to=request.sid)

# ==============================
# DISCONNECT
# ==============================
@socketio.on('disconnect')
def handle_disconnect():
    global waiting_player

    if request.sid in players:
        username = players[request.sid]
        room = rooms.get(request.sid)

        print(f"[-] {username} disconnected")

        # Remove player
        del players[request.sid]

        if request.sid in rooms:
            del rooms[request.sid]

        # Notify room
        if room:
            emit("message", f"[SERVER] {username} left the game", room=room)

        # Reset waiting player if needed
        if waiting_player == request.sid:
            waiting_player = None

# ==============================
# HOME
# ==============================
@app.route("/")
def home():
    return "🚀 Multiplayer Server Running"

# ==============================
# RUN
# ==============================
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
