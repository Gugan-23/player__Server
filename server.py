import socket
import threading

HOST = '0.0.0.0'
PORT = 5555
MAX_PLAYERS = 4

players = {}
lock = threading.Lock()

def broadcast(message, sender_conn=None):
    with lock:
        for conn in list(players):
            if conn != sender_conn:
                try:
                    conn.sendall(message.encode())
                except:
                    pass

def handle_player(conn, addr, player_id):
    name = f"Player{player_id}"
    with lock:
        players[conn] = name
    print(f"[+] {name} connected from {addr}")
    broadcast(f"[SERVER] {name} has joined! ({len(players)}/4 players)\n")
    conn.sendall(f"[SERVER] Welcome {name}! You are Player {player_id}\n".encode())

    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            msg = data.decode().strip()
            print(f"[{name}]: {msg}")
            broadcast(f"[{name}]: {msg}\n", sender_conn=conn)
    except:
        pass
    finally:
        # ✅ Remove player FIRST, then broadcast updated count
        with lock:
            if conn in players:
                del players[conn]
        conn.close()

        remaining = len(players)
        print(f"[-] {name} has LEFT the server  ({remaining}/4 players remaining)")  # ✅ Server log
        broadcast(f"[SERVER] ❌ {name} has left! ({remaining}/4 players remaining)\n")  # ✅ All clients notified

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(MAX_PLAYERS)
    print(f"[SERVER] Listening on port {PORT}, waiting for {MAX_PLAYERS} players...")

    player_id = 1
    while True:
        conn, addr = server.accept()
        if len(players) >= MAX_PLAYERS:
            conn.sendall(b"[SERVER] Sorry, server is full (4/4 players).\n")
            conn.close()
            continue
        thread = threading.Thread(target=handle_player, args=(conn, addr, player_id))
        thread.daemon = True
        thread.start()
        player_id += 1

if __name__ == "__main__":
    start_server()