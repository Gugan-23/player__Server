import eventlet
eventlet.monkey_patch()

from flask import Flask, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room
import random
import os

app = Flask(__name__, static_folder=".")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# ==============================
# GLOBALS
# ==============================
players = {}       # sid -> { username, room }
waiting_player = None
rooms = {}         # room_id -> { players, spins, scores, round, total_rounds, state }

WHEEL_SEGMENTS = [100, 200, 300, 400, 500, 50, 150, 250, 350, 450]

# ==============================
# SERVE UI
# ==============================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")

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
    username = data.get("username", f"Player{len(players) + 1}")
    players[request.sid] = {
        "username": username,
        "room": None
    }
    print(f"[+] {username} joined")

    if waiting_player is None:
        waiting_player = request.sid
        emit("state", {
            "status": "waiting",
            "message": "Waiting for opponent..."
        }, to=request.sid)
    else:
        room = f"room_{waiting_player}_{request.sid}"
        p1 = waiting_player
        p2 = request.sid

        join_room(room, sid=p1)
        join_room(room, sid=p2)

        players[p1]["room"] = room
        players[p2]["room"] = room

        rooms[room] = {
            "players": [p1, p2],
            "spins": {},
            "scores": {p1: 0, p2: 0},
            "round": 1,
            "total_rounds": 3,
            "state": "ready"
        }

        waiting_player = None

        emit("state", {
            "status": "matched",
            "room": room,
            "opponent": players[p1]["username"],
            "your_name": players[p2]["username"],
            "round": 1,
            "total_rounds": 3
        }, to=p2)

        emit("state", {
            "status": "matched",
            "room": room,
            "opponent": players[p2]["username"],
            "your_name": players[p1]["username"],
            "round": 1,
            "total_rounds": 3
        }, to=p1)

        print(f"[ROOM] {room}: {players[p1]['username']} vs {players[p2]['username']}")

# ==============================
# SPIN
# ==============================
@socketio.on('spin')
def handle_spin():
    sid = request.sid
    if sid not in players:
        return

    room_id = players[sid].get("room")
    if not room_id or room_id not in rooms:
        emit("error", {"message": "Not in a room"}, to=sid)
        return

    room = rooms[room_id]

    if sid in room["spins"]:
        emit("error", {"message": "Already spun this round!"}, to=sid)
        return

    result = random.choice(WHEEL_SEGMENTS)
    seg_index = WHEEL_SEGMENTS.index(result)
    seg_angle = 360 / len(WHEEL_SEGMENTS)
    # Spin at least 5 full rotations + land on segment
    degrees = (5 * 360) + random.randint(2000, 3600) + int(seg_angle * seg_index)

    room["spins"][sid] = result
    room["scores"][sid] = room["scores"].get(sid, 0) + result

    username = players[sid]["username"]
    print(f"[SPIN] {username} -> {result}")

    emit("spin_result", {
        "player": sid,
        "username": username,
        "value": result,
        "degrees": degrees
    }, to=room_id)

    # Both players spun?
    if len(room["spins"]) == len(room["players"]):
        p1, p2 = room["players"]
        s1 = room["spins"].get(p1, 0)
        s2 = room["spins"].get(p2, 0)
        total1 = room["scores"][p1]
        total2 = room["scores"][p2]

        if s1 > s2:
            round_winner = players[p1]["username"]
        elif s2 > s1:
            round_winner = players[p2]["username"]
        else:
            round_winner = "Tie"

        if room["round"] < room["total_rounds"]:
            room["round"] += 1
            room["spins"] = {}

            socketio.sleep(3)  # wait for spin animation
            emit("round_result", {
                "status": "next_round",
                "round_winner": round_winner,
                "p1": {"username": players[p1]["username"], "spin": s1, "total": total1},
                "p2": {"username": players[p2]["username"], "spin": s2, "total": total2},
                "next_round": room["round"],
                "total_rounds": room["total_rounds"]
            }, to=room_id)
        else:
            room["state"] = "done"
            if total1 > total2:
                winner = players[p1]["username"]
            elif total2 > total1:
                winner = players[p2]["username"]
            else:
                winner = "Tie"

            socketio.sleep(3)
            emit("game_over", {
                "status": "game_over",
                "winner": winner,
                "p1": {"username": players[p1]["username"], "total": total1},
                "p2": {"username": players[p2]["username"], "total": total2}
            }, to=room_id)
            print(f"[GAME OVER] Winner: {winner}")

# ==============================
# REMATCH
# ==============================
@socketio.on('rematch')
def handle_rematch():
    sid = request.sid
    room_id = players[sid].get("room")
    if not room_id or room_id not in rooms:
        return

    room = rooms[room_id]
    room["spins"] = {}
    room["scores"] = {p: 0 for p in room["players"]}
    room["round"] = 1
    room["state"] = "ready"

    emit("state", {
        "status": "rematch",
        "round": 1,
        "total_rounds": room["total_rounds"]
    }, to=room_id)

# ==============================
# DISCONNECT
# ==============================
@socketio.on('disconnect')
def handle_disconnect():
    global waiting_player
    sid = request.sid
    if sid not in players:
        return

    username = players[sid]["username"]
    room_id = players[sid].get("room")
    print(f"[-] {username} disconnected")

    if room_id and room_id in rooms:
        emit("state", {
            "status": "opponent_left",
            "message": f"{username} disconnected. Game ended."
        }, to=room_id)
        del rooms[room_id]

    del players[sid]
    if waiting_player == sid:
        waiting_player = None

# ==============================
# RUN
# ==============================
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
