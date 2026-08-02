import os
import re
import requests
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

def extract_direct_video(url):
    """Автоматически вытягивает прямую ссылку на .mp4 видео со страницы Kinovibe"""
    if not url.startswith("http"):
        return url
    
    if url.endswith(".mp4") or url.endswith(".m3u8"):
        return url

    print(f"[🔍] Парсим страницу Kinovibe: {url}")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://kinovibe.cc/'
        }
        res = requests.get(url, headers=headers, timeout=10)
        
        # Ищем прямую ссылку на .mp4 в коде страницы Киновайба
        matches = re.findall(r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8)[^\s"\'<>]*)', res.text)
        if matches:
            video_url = matches[0]
            print(f"[✅] Найдено видео: {video_url}")
            return video_url
    except Exception as e:
        print(f"[❌] Ошибка парсинга: {e}")
    
    return url

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
    raw_url = data.get("url")

    if action == "load" and raw_url:
        # Автоматически вытягиваем видео с Киновайба!
        direct_url = extract_direct_video(raw_url)
        room_state["current_url"] = direct_url
        room_state["current_time"] = 0
        room_state["is_playing"] = True
        
        data["url"] = direct_url
        await sio.emit('sync_event', data) # Отправляем ВСЕМ
        return

    if action == "play":
        room_state["is_playing"] = True
        room_state["current_time"] = time
    elif action == "pause":
        room_state["is_playing"] = False
        room_state["current_time"] = time
    elif action == "seek":
        room_state["current_time"] = time

    print(f"[SYNC] Owner action ({action}): {data}")
    await sio.emit('sync_event', data, skip_sid=sid)

if __name__ == '__main__':
    print(f"[READY] Server running! Active PIN: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
