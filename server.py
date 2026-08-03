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
    "last_proxied_url": "https://kinovibe.cc/",
    "current_video_url": None,
    "is_playing": False,
    "current_time": 0,
    "connected_count": 0
}

async def proxy_handler(request):
    target_url = request.query.get("url", "https://kinovibe.cc/")
    if not target_url.startswith("http"):
        target_url = "https://kinovibe.cc/" + target_url.lstrip("/")

    # Сервер запоминает актуальную страницу Киновайба!
    room_state["last_proxied_url"] = target_url
    print(f"[📍 PROXY TRACK] Текущая страница Киновайба: {target_url}")

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': 'https://kinovibe.cc/'
        }
        res = requests.get(target_url, headers=headers, timeout=10)
        html = res.text

        base_tag = f'<base href="{target_url}">'
        if "<head>" in html:
            html = html.replace("<head>", f"<head>{base_tag}")
        else:
            html = base_tag + html

        return web.Response(text=html, content_type='text/html', charset='utf-8')
    except Exception as e:
        return web.Response(text=f"Proxy Error: {e}", content_type='text/html', status=500)

app.router.add_get('/proxy', proxy_handler)

def extract_video_from_page(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': 'https://kinovibe.cc/'
        }
        res = requests.get(url, headers=headers, timeout=10)
        matches = re.findall(r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8)[^\s"\'<>]*)', res.text)
        return matches[0] if matches else None
    except Exception as e:
        print(f"[❌] Ошибка парсинга {url}: {e}")
        return None

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
    print(f"[?] Auth attempt SID: {sid} | PIN: '{pin}'")
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        print(f"[👑] OWNER AUTHENTICATED: {sid}")
        await sio.emit('auth_result', {'success': True}, to=sid)
    else:
        await sio.emit('auth_result', {'success': False, 'message': f"Неверный ПИН '{pin}'!"}, to=sid)

@sio.event
async def sync_action(sid, data):
    if sid != room_state["owner_sid"]:
        return

    action = data.get("action")

    if action == "start_current":
        # БЕРЕМ ПОСЛЕДНЮЮ СТРАНИЦУ КИНОВАЙБА АВТОМАТИЧЕСКИ!
        target_url = room_state["last_proxied_url"]
        print(f"[🚀 AUTO-START] Извлекаем видео с открытой страницы: {target_url}")
        
        video_url = extract_video_from_page(target_url)
        if video_url:
            room_state["cinema_mode"] = True
            room_state["current_video_url"] = video_url
            room_state["current_time"] = 0
            room_state["is_playing"] = True
            print(f"[🎬] УСПЕХ! Извлечено видео: {video_url}")
            await sio.emit('cinema_start', {'url': video_url})
        else:
            await sio.emit('parse_error', {'message': f"Не удалось автоматически извлечь видео со страницы: {target_url}"}, to=sid)

    elif action == "exit_cinema":
        room_state["cinema_mode"] = False
        await sio.emit('cinema_exit', {})

    elif action == "play":
        room_state["current_time"] = data.get("time", 0)
        await sio.emit('sync_event', {'action': 'play', 'time': room_state["current_time"]}, skip_sid=sid)

    elif action == "pause":
        room_state["current_time"] = data.get("time", 0)
        await sio.emit('sync_event', {'action': 'pause', 'time': room_state["current_time"]}, skip_sid=sid)

    elif action == "seek":
        room_state["current_time"] = data.get("time", 0)
        await sio.emit('sync_event', {'action': 'seek', 'time': room_state["current_time"]}, skip_sid=sid)

if __name__ == '__main__':
    print(f"[READY] Server v2.7 Debug running! PIN: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
