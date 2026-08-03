import os
import re
import asyncio
import requests
import socketio
from aiohttp import web

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

room_state = {
    "owner_sid": None,
    "cinema_mode": False,
    "current_url": None,
    "is_playing": False,
    "current_time": 0,
    "connected_count": 0
}

def parse_kinovibe_page(url):
    """Серверный парсинг Киновайба: забирает прямые ссылки на видео и серии"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Referer': 'https://kinovibe.cc/'
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        html = res.text
        
        # Поиск видеофайлов (.mp4 / .m3u8)
        matches = re.findall(r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8)[^\s"\'<>]*)', html)
        video_url = matches[0] if matches else None
        
        return {
            "success": True if video_url else False,
            "video_url": video_url,
            "raw_url": url
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@sio.event
async def connect(sid, environ):
    room_state["connected_count"] += 1
    print(f"[+] Client connected: {sid} | Total: {room_state['connected_count']}")
    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    if room_state["owner_sid"] == sid:
        room_state["owner_sid"] = None
        print("[!] Owner disconnected!")

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        print(f"[👑] OWNER AUTHENTICATED: {sid}")
        await sio.emit('auth_result', {'success': True}, to=sid)
    else:
        await sio.emit('auth_result', {'success': False, 'message': 'Неверный ПИН!'}, to=sid)

@sio.event
async def sync_action(sid, data):
    if sid != room_state["owner_sid"]:
        return

    action = data.get("action")

    if action == "parse_and_start":
        page_url = data.get("url")
        print(f"[🔍] Сервер парсит страницу: {page_url}")
        
        parsed = parse_kinovibe_page(page_url)
        if parsed["success"]:
            room_state["cinema_mode"] = True
            room_state["current_url"] = parsed["video_url"]
            room_state["current_time"] = 0
            room_state["is_playing"] = True
            print(f"[🎬] Видео найдено! Запуск кинотеатра: {parsed['video_url']}")
            await sio.emit('cinema_start', {'url': parsed["video_url"]})
        else:
            await sio.emit('parse_error', {'message': f"Не удалось извлечь видеопоток: {parsed.get('error', 'Видео не найдено')}"}, to=sid)

    elif action == "exit_cinema":
        room_state["cinema_mode"] = False
        room_state["current_url"] = None
        await sio.emit('cinema_exit', {})

    elif action == "play":
        room_state["is_playing"] = True
        room_state["current_time"] = data.get("time", 0)
        await sio.emit('sync_event', {'action': 'play', 'time': room_state["current_time"]}, skip_sid=sid)

    elif action == "pause":
        room_state["is_playing"] = False
        room_state["current_time"] = data.get("time", 0)
        await sio.emit('sync_event', {'action': 'pause', 'time': room_state["current_time"]}, skip_sid=sid)

    elif action == "seek":
        room_state["current_time"] = data.get("time", 0)
        await sio.emit('sync_event', {'action': 'seek', 'time': room_state["current_time"]}, skip_sid=sid)

if __name__ == '__main__':
    print(f"[READY] Server Auto-Parser running! Active PIN: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
