from flask import Flask, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

MAX_PLAYERS = 4
players = {}   # sid → {name, ip}

# 🔹 When client connects
@socketio.on('connect')
def handle_connect():
    if len(players) >= MAX_PLAYERS:
        return False  # ❌ Reject connection

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    print(f"[+] New connection from IP: {ip} | SID: {request.sid}")


# 🔹 When player joins
@socketio.on('join')
def handle_join(data):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    username = f"Player{len(players) + 1}"

    players[request.sid] = {
        "name": username,
        "ip": ip
    }

    print(f"[+] {username} joined from IP: {ip}")

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
    player = players.get(request.sid, {"name": "Unknown", "ip": "?"})
    username = player["name"]
    ip = player["ip"]

    print(f"[{username} | {ip}]: {msg}")

    emit('message',
         f"[{username}]: {msg}",
         broadcast=True,
         include_self=False)


# 🔹 When player disconnects
@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in players:
        player = players[request.sid]
        username = player["name"]
        ip = player["ip"]

        del players[request.sid]
        remaining = len(players)

        print(f"[-] {username} ({ip}) left ({remaining}/4)")

        emit('message',
             f"[SERVER] ❌ {username} has left! ({remaining}/4 players)",
             broadcast=True)


# 🔹 Basic route (required for Render)
@app.route("/")
def home():
    return "Multiplayer Game Server Running 🚀"


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
