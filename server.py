import os
import asyncio
import socketio
from aiohttp import web

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

room_state = {
    "owner_sid": None,
    "current_url": "https://kinovibe.cc/",
    "connected_count": 0
}

sleep_timer_task = None

async def auto_sleep_timer():
    print("[⏳] [TIMER] Запущен 30-минутный таймер сна...")
    await asyncio.sleep(1800)
    room_state["current_url"] = "https://kinovibe.cc/"
    room_state["owner_sid"] = None
    print("[💤] [SLEEP] Память комнаты очищена.")

@sio.event
async def connect(sid, environ):
    global sleep_timer_task
    room_state["connected_count"] += 1
    ua = environ.get("HTTP_USER_AGENT", "Unknown")[:40]
    print(f"[+] [CONNECT] SID: {sid} | Total: {room_state['connected_count']} | UA: {ua}")
    
    if sleep_timer_task and not sleep_timer_task.done():
        sleep_timer_task.cancel()
        
    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    global sleep_timer_task
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    print(f"[-] [DISCONNECT] SID: {sid} | Left: {room_state['connected_count']}")

    if room_state["owner_sid"] == sid:
        room_state["owner_sid"] = None
        print("[!] [OWNER_LEFT] Owner disconnected!")

    if room_state["connected_count"] == 0:
        sleep_timer_task = asyncio.create_task(auto_sleep_timer())

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    print(f"[?] [AUTH_ATTEMPT] SID: {sid} | Received PIN: '{pin}' | Active PIN: '{OWNER_PIN}'")
    
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        print(f"[👑] [AUTH_SUCCESS] SID: {sid}")
        await sio.emit('auth_result', {'success': True, 'message': 'Успешная авторизация'}, to=sid)
    else:
        print(f"[❌] [AUTH_FAILED] SID: {sid} | Received: '{pin}', Expected: '{OWNER_PIN}'")
        await sio.emit('auth_result', {
            'success': False, 
            'message': f"Отказ входа! Введен ПИН '{pin}', но активный ПИН на сервере равен '{OWNER_PIN}'."
        }, to=sid)

@sio.event
async def sync_action(sid, data):
    if sid != room_state["owner_sid"]:
        print(f"[⚠️] [UNAUTHORIZED] SID: {sid} попытался выполнить действие без прав Owner!")
        return

    action = data.get("action")
    if action == "load":
        url = data.get("url")
        room_state["current_url"] = url
        print(f"[🎬] [SYNC_LOAD] URL: {url}")
        await sio.emit('sync_event', {'action': 'load', 'url': url}, skip_sid=sid)

if __name__ == '__main__':
    print(f"[READY] Server v2.4 Debug running! PIN: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
