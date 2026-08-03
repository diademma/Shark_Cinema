import os
import re
import base64
import asyncio
import socketio
from aiohttp import web, ClientSession

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

room_state = {
    "owner_sid": None,
    "mode": "iframe",
    "current_url": "https://kinovibe.cc/",
    "connected_count": 0
}

# --- УМНЫЙ ДЕКОДЕР И ФИЛЬТР ССЫЛОК KINOVIBE ---
def parse_kvb_stream(html):
    # 1. Находим все ссылки на mp4 и m3u8
    raw_urls = re.findall(r'https?://[^\s"\'<>]+(?:\.mp4|\.m3u8)[^\s"\'<>]*', html)
    
    # ФИЛЬТР: Отбрасываем трейлеры! Ищем только реальный контент
    full_movies = [u for u in raw_urls if "/trailer/" not in u and "trailer" not in u.lower()]
    
    if full_movies:
        return full_movies[0]

    # 2. Если ссылки в закодированном Base64 формате Playerjs (file:"aHR0c...")
    b64_matches = re.findall(r'file\s*:\s*["\']([^"\'\s]+)["\']', html)
    for b64_str in b64_matches:
        cleaned_b64 = re.sub(r'^#[0-9]', '', b64_str) # Чистим префиксы Playerjs
        try:
            decoded = base64.b64decode(cleaned_b64).decode('utf-8', errors='ignore')
            decoded_urls = re.findall(r'https?://[^\s"\'<>]+(?:\.mp4|\.m3u8)[^\s"\'<>]*', decoded)
            valid_movies = [u for u in decoded_urls if "/trailer/" not in u and "trailer" not in u.lower()]
            if valid_movies:
                return valid_movies[0]
        except Exception:
            pass
            
    return None

@sio.event
async def connect(sid, environ):
    room_state["connected_count"] += 1
    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    if room_state["owner_sid"] == sid:
        room_state["owner_sid"] = None

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        await sio.emit('auth_result', {'success': True}, to=sid)
    else:
        await sio.emit('auth_result', {'success': False}, to=sid)

@sio.event
async def player_action(sid, data):
    if sid != room_state["owner_sid"]: return
    action = data.get("action")
    if action == "load_iframe":
        room_state["mode"] = "iframe"
        room_state["current_url"] = data.get("url")
    elif action == "load_video":
        room_state["mode"] = "video"
        room_state["current_url"] = data.get("url")
    await sio.emit('player_command', data, skip_sid=sid)

@sio.event
async def extract_magic(sid, data):
    if sid != room_state["owner_sid"]: return
        
    url = data.get("url", "")
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Запуск Умного Парсера v2.7...', 'details': url}, to=sid)

    # Если овнер сразу дал прямую ссылку на mp4/m3u8 (не трейлер)
    if (".m3u8" in url or ".mp4" in url) and "/trailer/" not in url:
        room_state["mode"] = "video"
        room_state["current_url"] = url
        await sio.emit('player_command', {'action': 'load_video', 'url': url})
        return

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kinovibe.cc/"
        }
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                
                # Парсим поток
                found_video = parse_kvb_stream(html)
                
                if found_video:
                    room_state["mode"] = "video"
                    room_state["current_url"] = found_video
                    await sio.emit('player_command', {'action': 'load_video', 'url': found_video})
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Найдено полноразмерное видео (без трейлера)!', 'details': found_video}, to=sid)
                    return

                # Если на главной ничего нет, пробуем пройти по внутренним iframe
                match_iframe = re.search(r'<iframe[^>]+src=["\'](https?://[^"\']+)["\']', html)
                if match_iframe:
                    iframe_url = match_iframe.group(1)
                    await sio.emit('server_log', {'type': 'WARN', 'msg': 'Сканирую внутренний iframe...', 'details': iframe_url}, to=sid)
                    
                    async with session.get(iframe_url, timeout=10) as r2:
                        html2 = await r2.text()
                        found_video2 = parse_kvb_stream(html2)
                        if found_video2:
                            room_state["mode"] = "video"
                            room_state["current_url"] = found_video2
                            await sio.emit('player_command', {'action': 'load_video', 'url': found_video2})
                            await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Видео вытащено из iframe!', 'details': found_video2}, to=sid)
                            return
                            
                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Видео не найдено', 'details': 'Страница не содержит прямых видеопотоков.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка парсера Python!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)
