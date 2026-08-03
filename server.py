import os
import re
import asyncio
import socketio
from aiohttp import web, ClientSession

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

# Теперь храним 2 режима: iframe (сайт) или video (syncplay)
room_state = {
    "owner_sid": None,
    "mode": "iframe", # 'iframe' или 'video'
    "current_url": "https://kinovibe.cc/",
    "connected_count": 0
}

@sio.event
async def connect(sid, environ):
    room_state["connected_count"] += 1
    print(f"[+] [CONNECT] SID: {sid} | Total: {room_state['connected_count']}")
    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    if room_state["owner_sid"] == sid:
        room_state["owner_sid"] = None
        print("[!] [OWNER_LEFT] Owner disconnected!")

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        await sio.emit('auth_result', {'success': True}, to=sid)
    else:
        await sio.emit('auth_result', {'success': False, 'message': 'Неверный ПИН!'}, to=sid)

# --- НОВАЯ СИНХРОНИЗАЦИЯ ПЛЕЕРА ---
@sio.event
async def player_action(sid, data):
    if sid != room_state["owner_sid"]:
        return # Только Owner может управлять
    
    action = data.get("action")
    if action == "load_iframe":
        room_state["mode"] = "iframe"
        room_state["current_url"] = data.get("url")
    elif action == "load_video":
        room_state["mode"] = "video"
        room_state["current_url"] = data.get("url")
    
    print(f"[🎬] [SYNC] Action: {action} | Data: {data}")
    # Рассылаем всем, кроме самого Овнера
    await sio.emit('player_command', data, skip_sid=sid)

# --- МАГИЧЕСКИЙ ЭКСТРАКТОР (ПАРСЕР СЫРОГО ВИДЕО) ---
@sio.event
async def extract_magic(sid, data):
    if sid != room_state["owner_sid"]:
        return
        
    url = data.get("url", "")
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Python начал сканирование URL:', 'details': url}, to=sid)
    print(f"[🔍] Extraction request: {url}")

    # Если овнер сразу вставил .m3u8 или .mp4 - просто включаем его
    if ".m3u8" in url or ".mp4" in url:
        room_state["mode"] = "video"
        room_state["current_url"] = url
        await sio.emit('player_command', {'action': 'load_video', 'url': url})
        await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Это прямая ссылка, запускаю SyncPlay!', 'details': ''}, to=sid)
        return

    # Если это страница, пытаемся вытащить iframe или m3u8
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                html = await resp.text()
                
                # Ищем сырую ссылку m3u8
                match_m3u8 = re.search(r'(https?://[^\s"\'<>]+(?:m3u8|mp4)[^\s"\']*)', html)
                if match_m3u8:
                    video_url = match_m3u8.group(1)
                    room_state["mode"] = "video"
                    room_state["current_url"] = video_url
                    await sio.emit('player_command', {'action': 'load_video', 'url': video_url})
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Найдено сырое видео!', 'details': video_url}, to=sid)
                    return
                
                # Ищем встроенный iframe (балансеры типа kodik/alloha)
                match_iframe = re.search(r'<iframe[^>]+src=["\'](https?://[^"\']+)["\']', html)
                if match_iframe:
                    iframe_url = match_iframe.group(1)
                    await sio.emit('server_log', {'type': 'WARN', 'msg': 'Нашел балансер, паршу глубже...', 'details': iframe_url}, to=sid)
                    
                    async with session.get(iframe_url, timeout=10) as r2:
                        html2 = await r2.text()
                        match2 = re.search(r'(https?://[^\s"\'<>]+(?:m3u8|mp4)[^\s"\']*)', html2)
                        if match2:
                            video_url = match2.group(1)
                            room_state["mode"] = "video"
                            room_state["current_url"] = video_url
                            await sio.emit('player_command', {'action': 'load_video', 'url': video_url})
                            await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Видео вытащено из балансера!', 'details': video_url}, to=sid)
                            return
                            
                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Видео не найдено :(', 'details': 'Плеер скрыт сложной JS защитой. Вставь прямую ссылку.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка парсера (Python)!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    print(f"[READY] Server v2.5 SyncPlay | PIN: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
