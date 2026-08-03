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

# --- ВИДЕО-ТУННЕЛЬ / ПРОКСИ (Обход 403 и CORS) ---
async def proxy_video(request):
    target_url = request.query.get("url")
    if not target_url:
        return web.Response(status=400, text="Missing url param")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://kinovibe.cc/",
        "Origin": "https://kinovibe.cc"
    }

    # Пробрасываем заголовок перемотки (Range)
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    try:
        async with ClientSession() as session:
            async with session.get(target_url, headers=headers) as resp:
                proxy_resp = web.StreamResponse(
                    status=resp.status,
                    headers={
                        "Content-Type": resp.headers.get("Content-Type", "video/mp4"),
                        "Access-Control-Allow-Origin": "*",
                        "Accept-Ranges": resp.headers.get("Accept-Ranges", "bytes"),
                        "Content-Length": resp.headers.get("Content-Length", ""),
                        "Content-Range": resp.headers.get("Content-Range", "")
                    }
                )
                # Удаляем пустые заголовки
                proxy_resp.headers = {k: v for k, v in proxy_resp.headers.items() if v}
                
                await proxy_resp.prepare(request)
                
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    await proxy_resp.write(chunk)

                await proxy_resp.write_eof()
                return proxy_resp
    except Exception as e:
        print(f"[❌] Proxy error: {e}")
        return web.Response(status=500, text=str(e))

# Регистрируем роут прокси в aiohttp
app.router.add_get('/proxy_video', proxy_video)

def parse_kvb_stream(html):
    raw_urls = re.findall(r'https?://[^\s"\'<>]+(?:\.mp4|\.m3u8)[^\s"\'<>]*', html)
    full_movies = [u for u in raw_urls if "/trailer/" not in u and "trailer" not in u.lower()]
    if full_movies:
        return full_movies[0]

    b64_matches = re.findall(r'file\s*:\s*["\']([^"\'\s]+)["\']', html)
    for b64_str in b64_matches:
        cleaned_b64 = re.sub(r'^#[0-9]', '', b64_str)
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
        
    url = data.get("url", "").strip()
    
    if ".mp4" in url or ".m3u8" in url or "kvb.cool" in url:
        await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Прямое видео отправлено через Прокси-Туннель!', 'details': url}, to=sid)
        room_state["mode"] = "video"
        room_state["current_url"] = url
        await sio.emit('player_command', {'action': 'load_video', 'url': url})
        return

    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Сканирование страницы...', 'details': url}, to=sid)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://kinovibe.cc/"
        }
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                found_video = parse_kvb_stream(html)
                
                if found_video:
                    room_state["mode"] = "video"
                    room_state["current_url"] = found_video
                    await sio.emit('player_command', {'action': 'load_video', 'url': found_video})
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Видео найдено и запущен Туннель!', 'details': found_video}, to=sid)
                    return
                            
                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Видео не найдено', 'details': 'Вставь прямую ссылку из DOM в поле.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка запроса!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)
