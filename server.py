from flask import Flask, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

MAX_PLAYERS = 4
players = {}

# 🔹 Connect
@socketio.on('connect')
def handle_connect():
    if len(players) >= MAX_PLAYERS:
        return False

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    print(f"[+] New connection from IP: {ip}")

# 🔹 Join
@socketio.on('join')
def handle_join(data):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    username = f"Player{len(players)+1}"

    players[request.sid] = {"name": username, "ip": ip}

    print(f"[+] {username} joined from IP: {ip}")

    emit('message',
         f"[SERVER] Welcome {username}! ({len(players)}/4 players)",
         to=request.sid)

    emit('message',
         f"[SERVER] {username} joined!",
         broadcast=True,
         include_self=False)

# 🔹 Message
@socketio.on('message')
def handle_message(msg):
    if request.sid not in players:
        return

    player = players[request.sid]
    print(f"[{player['name']}]: {msg}")

    emit('message',
         f"[{player['name']}]: {msg}",
         broadcast=True,
         include_self=False)

# 🔹 Disconnect
@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in players:
        player = players[request.sid]
        del players[request.sid]

        print(f"[-] {player['name']} left")

        emit('message',
             f"[SERVER] {player['name']} left",
             broadcast=True)

@app.route("/")
def home():
    return "Server Running 🚀"

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
