import os
import socketio
from aiohttp import web

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

room_state = {
    "owner_sid": None,
    "current_url": None,
    "is_playing": False,
    "current_time": 0
}

@sio.event
async def connect(sid, environ):
    print(f"[+] Client connected: {sid}")
    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    print(f"[-] Client disconnected: {sid}")
    if room_state["owner_sid"] == sid:
        room_state["owner_sid"] = None
        print("[!] Owner disconnected!")

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        print(f"[OK] OWNER AUTHENTICATED! ID: {sid}")
        await sio.emit('auth_result', {'success': True}, to=sid)
    else:
        print(f"[FAIL] Incorrect PIN from ID {sid}: got '{pin}'")
        await sio.emit('auth_result', {'success': False, 'message': 'Incorrect PIN'}, to=sid)

@sio.event
async def sync_action(sid, data):
    if sid != room_state["owner_sid"]:
        return

    action = data.get("action")
    time = data.get("time", 0)
    url = data.get("url")

    if action == "play":
        room_state["is_playing"] = True
        room_state["current_time"] = time
    elif action == "pause":
        room_state["is_playing"] = False
        room_state["current_time"] = time
    elif action == "seek":
        room_state["current_time"] = time
    elif action == "load":
        room_state["current_url"] = url
        room_state["current_time"] = 0
        room_state["is_playing"] = False

    print(f"[SYNC] Owner action ({action}): {data}")
    await sio.emit('sync_event', data, skip_sid=sid)

if __name__ == '__main__':
    print(f"[READY] Server running! Active PIN: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
