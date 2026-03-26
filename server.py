import eventlet
eventlet.monkey_patch()

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, send_from_directory, jsonify, session
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient, DESCENDING
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import random
import os
import uuid

app = Flask(__name__, static_folder=".")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet"
)

# ==============================
# ENV CONFIG
# ==============================

MONGO_URI = os.getenv("MONGO_URI") or "mongodb://127.0.0.1:27017/"
DB_NAME   = os.getenv("DB_NAME") or "spinbattle"
SECRET_KEY= os.getenv("SECRET_KEY") or "super_secret_key_123"

PORT      = int(os.getenv("PORT") or 5000)

DEBUG     = os.getenv("DEBUG", "false").lower() in ["true", "1", "yes"]
app.secret_key = SECRET_KEY

print(f"[ENV] DB={DB_NAME}  PORT={PORT}  DEBUG={DEBUG}")

# ==============================
# MONGODB SETUP
# ==============================
mongo_client = MongoClient(MONGO_URI)
db           = mongo_client[DB_NAME]

players_col = db["players"]
rooms_col   = db["rooms"]

print(f"[DB] Connected → {db.name}")

# ==============================
# IN-MEMORY
# ==============================
sessions       = {}   # sid -> { username, room_id }
active_rooms   = {}   # room_id -> { ... }
waiting_player = None

WHEEL_SEGMENTS = [100, 200, 300, 400, 500, 50, 150, 250, 350, 450]


# ==============================
# AUTH HELPERS
# ==============================

def get_player(username):
    return players_col.find_one({"username": username})


def serialize_player(doc):
    if not doc:
        return {}
    doc = dict(doc)
    doc.pop("_id", None)
    doc.pop("password_hash", None)
    for k in ("created_at", "last_played"):
        if doc.get(k):
            doc[k] = str(doc[k])
    return doc


def player_stats(doc):
    return {k: doc.get(k, 0) for k in
            ("games_played", "games_won", "total_points", "highest_spin")}


# ==============================
# DB HELPERS
# ==============================

def create_room_doc(room_id, p1_name, p2_name, total_rounds):
    doc = {
        "room_id":       room_id,
        "players":       [p1_name, p2_name],
        "scores":        {p1_name: 0, p2_name: 0},
        "round_history": [],
        "current_round": 1,
        "total_rounds":  total_rounds,
        "state":         "active",
        "winner":        None,
        "created_at":    datetime.utcnow(),
        "finished_at":   None
    }
    rooms_col.insert_one(doc)
    print(f"[DB] Room created: {room_id}")
    return doc


def record_round(room_id, round_num, p1_name, s1, p2_name, s2, round_winner, name_scores):
    rooms_col.update_one(
        {"room_id": room_id},
        {
            "$push": {"round_history": {
                "round":        round_num,
                "spins":        {p1_name: s1, p2_name: s2},
                "round_winner": round_winner
            }},
            "$set": {
                "scores":        name_scores,
                "current_round": round_num + 1
            }
        }
    )


def finalize_room(room_id, winner, name_scores):
    rooms_col.update_one(
        {"room_id": room_id},
        {"$set": {
            "state":       "done",
            "winner":      winner,
            "scores":      name_scores,
            "finished_at": datetime.utcnow()
        }}
    )
    print(f"[DB] Room {room_id} → winner: {winner}")


def update_player_stats(username, points, result, best_spin):
    inc = {"total_points": points, "games_played": 1}
    if result == "win":
        inc["games_won"]  = 1
    elif result == "loss":
        inc["games_lost"] = 1
    else:
        inc["games_tied"] = 1

    players_col.update_one(
        {"username": username},
        {
            "$inc": inc,
            "$max": {"highest_spin": best_spin},
            "$set": {"last_played": datetime.utcnow()}
        }
    )
    print(f"[DB] {username} → +{points}pts, {result}, best={best_spin}")


# ==============================
# REST — AUTH
# ==============================

@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/register", methods=["POST"])
def api_register():
    data     = request.get_json() or {}
    username = data.get("username", "").strip()[:20]
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400
    if players_col.find_one({"username": username}):
        return jsonify({"error": "Username already taken"}), 409

    doc = {
        "username":      username,
        "password_hash": generate_password_hash(password),
        "total_points":  0,
        "games_played":  0,
        "games_won":     0,
        "games_lost":    0,
        "games_tied":    0,
        "highest_spin":  0,
        "created_at":    datetime.utcnow(),
        "last_played":   None
    }
    players_col.insert_one(doc)
    print(f"[REGISTER] New player: {username}")
    return jsonify({"success": True, "message": "Account created!", "username": username})


@app.route("/api/login", methods=["POST"])
def api_login():
    data     = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    player = players_col.find_one({"username": username})
    if not player:
        return jsonify({"error": "Player not found"}), 404
    if not check_password_hash(player["password_hash"], password):
        return jsonify({"error": "Incorrect password"}), 401

    print(f"[LOGIN] {username}")
    return jsonify({
        "success":  True,
        "username": username,
        "stats":    player_stats(player)
    })


@app.route("/api/leaderboard")
def api_leaderboard():
    top = list(players_col.find(
        {},
        {"_id": 0, "username": 1, "total_points": 1,
         "games_won": 1, "games_played": 1, "highest_spin": 1}
    ).sort("total_points", DESCENDING).limit(10))
    return jsonify(top)


@app.route("/api/player/<username>")
def api_player(username):
    doc = players_col.find_one({"username": username})
    if not doc:
        return jsonify({"error": "Player not found"}), 404
    return jsonify(serialize_player(doc))


@app.route("/api/room/<room_id>")
def api_room(room_id):
    doc = rooms_col.find_one({"room_id": room_id}, {"_id": 0})
    if not doc:
        return jsonify({"error": "Room not found"}), 404
    doc["created_at"]  = str(doc.get("created_at", ""))
    doc["finished_at"] = str(doc.get("finished_at", ""))
    return jsonify(doc)


@app.route("/api/rooms")
def api_rooms():
    docs = list(rooms_col.find(
        {},
        {"_id": 0, "room_id": 1, "players": 1, "scores": 1,
         "winner": 1, "state": 1, "created_at": 1, "total_rounds": 1}
    ).sort("created_at", DESCENDING).limit(30))
    for d in docs:
        d["created_at"] = str(d.get("created_at", ""))
    return jsonify(docs)


# ==============================
# SOCKET — CONNECT
# ==============================

@socketio.on("connect")
def handle_connect():
    print(f"[+] Connected: {request.sid}")


# ==============================
# SOCKET — JOIN (after login)
# ==============================

@socketio.on("join")
def handle_join(data):
    global waiting_player
    sid      = request.sid
    username = data.get("username", "").strip()[:20]

    if not username:
        emit("error", {"message": "Must provide username"}, to=sid)
        return

    # Verify player exists in DB
    player_doc = get_player(username)
    if not player_doc:
        emit("error", {"message": "Player not found. Please login again."}, to=sid)
        return

    # If this player was already in sessions (reconnect), clean up
    for old_sid, info in list(sessions.items()):
        if info["username"] == username and old_sid != sid:
            del sessions[old_sid]
            break

    sessions[sid] = {
        "username": username,
        "room_id":  None,
    }
    print(f"[JOIN] {username} (sid={sid})")

    stats = player_stats(player_doc)
    emit("state", {
        "status":  "waiting",
        "message": "Looking for an opponent...",
        "stats":   stats
    }, to=sid)

    if waiting_player is None or waiting_player == sid:
        waiting_player = sid
        return

    # ── Match found ──
    p1_sid  = waiting_player
    p2_sid  = sid

    # Safety: make sure p1 still connected
    if p1_sid not in sessions:
        waiting_player = sid
        return

    p1_name = sessions[p1_sid]["username"]
    p2_name = sessions[p2_sid]["username"]

    if p1_name == p2_name:
        waiting_player = sid
        emit("error", {"message": "Cannot play against yourself"}, to=sid)
        return

    ts           = int(datetime.utcnow().timestamp())
    room_id      = f"room_{p1_sid[-6:]}_{p2_sid[-6:]}_{ts}"
    total_rounds = 3

    join_room(room_id, sid=p1_sid)
    join_room(room_id, sid=p2_sid)
    sessions[p1_sid]["room_id"] = room_id
    sessions[p2_sid]["room_id"] = room_id

    active_rooms[room_id] = {
        "players":      [p1_sid, p2_sid],
        "names":        {p1_sid: p1_name, p2_sid: p2_name},
        "spins":        {},
        "scores":       {p1_sid: 0, p2_sid: 0},
        "highest_spin": {p1_sid: 0, p2_sid: 0},
        "round":        1,
        "total_rounds": total_rounds,
        "state":        "ready"
    }

    create_room_doc(room_id, p1_name, p2_name, total_rounds)
    waiting_player = None

    p1_doc = get_player(p1_name)
    p2_doc = get_player(p2_name)

    emit("state", {
        "status":         "matched",
        "room":           room_id,
        "your_name":      p2_name,
        "opponent":       p1_name,
        "round":          1,
        "total_rounds":   total_rounds,
        "my_stats":       player_stats(p2_doc),
        "opponent_stats": player_stats(p1_doc)
    }, to=p2_sid)

    emit("state", {
        "status":         "matched",
        "room":           room_id,
        "your_name":      p1_name,
        "opponent":       p2_name,
        "round":          1,
        "total_rounds":   total_rounds,
        "my_stats":       player_stats(p1_doc),
        "opponent_stats": player_stats(p2_doc)
    }, to=p1_sid)

    print(f"[ROOM] {room_id}: {p1_name} vs {p2_name}")


# ==============================
# SOCKET — SPIN
# ==============================

@socketio.on("spin")
def handle_spin():
    sid = request.sid
    if sid not in sessions:
        return

    room_id = sessions[sid].get("room_id")
    if not room_id or room_id not in active_rooms:
        emit("error", {"message": "Not in a room"}, to=sid)
        return

    room = active_rooms[room_id]
    if room["state"] == "done":
        return
    if sid in room["spins"]:
        emit("error", {"message": "Already spun this round!"}, to=sid)
        return

    result    = random.choice(WHEEL_SEGMENTS)
    seg_index = WHEEL_SEGMENTS.index(result)
    seg_angle = 360 / len(WHEEL_SEGMENTS)
    degrees   = (5 * 360) + random.randint(2000, 3600) + int(seg_angle * seg_index)

    room["spins"][sid]         = result
    room["scores"][sid]       += result
    room["highest_spin"][sid]  = max(room["highest_spin"].get(sid, 0), result)

    username = sessions[sid]["username"]
    print(f"[SPIN] {username} → {result}")

    emit("spin_result", {
        "player":   sid,
        "username": username,
        "value":    result,
        "degrees":  degrees
    }, to=room_id)

    if len(room["spins"]) < len(room["players"]):
        return

    p1_sid, p2_sid = room["players"]
    p1_name = sessions[p1_sid]["username"]
    p2_name = sessions[p2_sid]["username"]
    s1 = room["spins"].get(p1_sid, 0)
    s2 = room["spins"].get(p2_sid, 0)
    t1 = room["scores"][p1_sid]
    t2 = room["scores"][p2_sid]

    round_winner = p1_name if s1 > s2 else (p2_name if s2 > s1 else "Tie")

    record_round(room_id, room["round"], p1_name, s1, p2_name, s2,
                 round_winner, {p1_name: t1, p2_name: t2})

    if room["round"] < room["total_rounds"]:
        room["round"] += 1
        room["spins"]  = {}
        socketio.sleep(3)
        emit("round_result", {
            "status":       "next_round",
            "round_winner": round_winner,
            "p1":           {"username": p1_name, "spin": s1, "total": t1},
            "p2":           {"username": p2_name, "spin": s2, "total": t2},
            "next_round":   room["round"],
            "total_rounds": room["total_rounds"]
        }, to=room_id)
        return

    # Game over
    room["state"] = "done"
    winner = p1_name if t1 > t2 else (p2_name if t2 > t1 else "Tie")
    finalize_room(room_id, winner, {p1_name: t1, p2_name: t2})

    for p_sid, p_name, p_total in [(p1_sid, p1_name, t1), (p2_sid, p2_name, t2)]:
        res = "tie" if winner == "Tie" else ("win" if winner == p_name else "loss")
        update_player_stats(p_name, p_total, res, room["highest_spin"].get(p_sid, 0))

    socketio.sleep(3)
    emit("game_over", {
        "status":  "game_over",
        "winner":  winner,
        "room_id": room_id,
        "p1":      {"username": p1_name, "total": t1},
        "p2":      {"username": p2_name, "total": t2}
    }, to=room_id)
    print(f"[GAME OVER] {room_id} → {winner}")


# ==============================
# SOCKET — REMATCH
# ==============================

@socketio.on("rematch")
def handle_rematch():
    sid     = request.sid
    room_id = sessions[sid].get("room_id")
    if not room_id or room_id not in active_rooms:
        return

    old_room       = active_rooms[room_id]
    total_rounds   = old_room["total_rounds"]
    p1_sid, p2_sid = old_room["players"]
    p1_name = sessions[p1_sid]["username"]
    p2_name = sessions[p2_sid]["username"]

    ts          = int(datetime.utcnow().timestamp())
    new_room_id = f"room_{p1_sid[-6:]}_{p2_sid[-6:]}_{ts}"

    join_room(new_room_id, sid=p1_sid)
    join_room(new_room_id, sid=p2_sid)
    sessions[p1_sid]["room_id"] = new_room_id
    sessions[p2_sid]["room_id"] = new_room_id

    active_rooms[new_room_id] = {
        "players":      [p1_sid, p2_sid],
        "names":        {p1_sid: p1_name, p2_sid: p2_name},
        "spins":        {},
        "scores":       {p1_sid: 0, p2_sid: 0},
        "highest_spin": {p1_sid: 0, p2_sid: 0},
        "round":        1,
        "total_rounds": total_rounds,
        "state":        "ready"
    }

    del active_rooms[room_id]
    create_room_doc(new_room_id, p1_name, p2_name, total_rounds)

    p1_doc = get_player(p1_name)
    p2_doc = get_player(p2_name)

    emit("state", {
        "status":         "rematch",
        "room":           new_room_id,
        "your_name":      p2_name,
        "opponent":       p1_name,
        "round":          1,
        "total_rounds":   total_rounds,
        "my_stats":       player_stats(p2_doc),
        "opponent_stats": player_stats(p1_doc)
    }, to=p2_sid)

    emit("state", {
        "status":         "rematch",
        "room":           new_room_id,
        "your_name":      p1_name,
        "opponent":       p2_name,
        "round":          1,
        "total_rounds":   total_rounds,
        "my_stats":       player_stats(p1_doc),
        "opponent_stats": player_stats(p2_doc)
    }, to=p1_sid)

    print(f"[REMATCH] {new_room_id}: {p1_name} vs {p2_name}")


# ==============================
# SOCKET — LEADERBOARD
# ==============================

@socketio.on("get_leaderboard")
def handle_leaderboard():
    top = list(players_col.find(
        {},
        {"_id": 0, "username": 1, "total_points": 1,
         "games_won": 1, "games_played": 1, "highest_spin": 1}
    ).sort("total_points", DESCENDING).limit(10))
    emit("leaderboard", {"data": top}, to=request.sid)


# ==============================
# SOCKET — DISCONNECT
# ==============================

@socketio.on("disconnect")
def handle_disconnect():
    global waiting_player
    sid = request.sid
    if sid not in sessions:
        return

    username = sessions[sid]["username"]
    room_id  = sessions[sid].get("room_id")
    print(f"[-] {username} disconnected")

    if room_id and room_id in active_rooms:
        room           = active_rooms[room_id]
        p1_sid, p2_sid = room["players"]
        p1_name = sessions.get(p1_sid, {}).get("username", "?")
        p2_name = sessions.get(p2_sid, {}).get("username", "?")
        if room["state"] != "done":
            finalize_room(room_id, "abandoned", {
                p1_name: room["scores"].get(p1_sid, 0),
                p2_name: room["scores"].get(p2_sid, 0)
            })
        emit("state", {
            "status":  "opponent_left",
            "message": f"{username} disconnected. Game ended."
        }, to=room_id)
        del active_rooms[room_id]

    del sessions[sid]
    if waiting_player == sid:
        waiting_player = None


# ==============================
# RUN
# ==============================

if __name__ == "__main__":
    players_col.create_index("username", unique=True)
    rooms_col.create_index("room_id",    unique=True)
    rooms_col.create_index("state")
    rooms_col.create_index("created_at")
    print("[DB] Indexes ready.")
    socketio.run(app, host="0.0.0.0", port=PORT, debug=DEBUG)
