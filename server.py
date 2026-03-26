from flask import Flask, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

MAX_PLAYERS = 4
players = {}   # sid → player_name
player_count = 0

# 🔹 When client connects
@socketio.on('connect')
def handle_connect():
    global player_count

    if len(players) >= MAX_PLAYERS:
        return False  # ❌ Reject connection

    print("[+] New connection:", request.sid)


# 🔹 When player joins
@socketio.on('join')
def handle_join(data):
    global player_count

    username = f"Player{len(players) + 1}"
    players[request.sid] = username

    print(f"[+] {username} joined")

    emit('message',
         f"[SERVER] Welcome {username}! ({len(players)}/4 players)",
         to=request.sid)

    emit('message',
         f"[SERVER] {username} has joined! ({len(players)}/4 players)",
         broadcast=True,
         include_self=False)


# 🔹 Receive message
@socketio.on('message')
def handle_message(msg):
    username = players.get(request.sid, "Unknown")
    print(f"[{username}]: {msg}")

    emit('message',
         f"[{username}]: {msg}",
         broadcast=True,
         include_self=False)


# 🔹 When player disconnects
@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in players:
        username = players[request.sid]
        del players[request.sid]

        remaining = len(players)

        print(f"[-] {username} left ({remaining}/4)")

        emit('message',
             f"[SERVER] ❌ {username} has left! ({remaining}/4 players)",
             broadcast=True)


# 🔹 Basic route (required for Render)
@app.route("/")
def home():
    return "Multiplayer Game Server Running 🚀"


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
