import os
import re
import json
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
    "playlist": [],
    "current_ep_index": 0,
    "connected_count": 0
}

# --- ВИДЕО ТУННЕЛЬ С ПОДДЕРЖКОЙ RANGE И REFERER ---
async def proxy_video(request):
    target_url = request.query.get("url")
    if not target_url: return web.Response(status=400, text="Missing url")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://kinovibe.cc/",
        "Origin": "https://kinovibe.cc"
    }

    range_header = request.headers.get("Range")
    if range_header: headers["Range"] = range_header

    try:
        async with ClientSession() as session:
            async with session.get(target_url, headers=headers, allow_redirects=True) as resp:
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
                proxy_resp.headers = {k: v for k, v in proxy_resp.headers.items() if v}
                await proxy_resp.prepare(request)
                
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    await proxy_resp.write(chunk)

                await proxy_resp.write_eof()
                return proxy_resp
    except Exception as e:
        return web.Response(status=500, text=str(e))

app.router.add_get('/proxy_video', proxy_video)

# --- ПАРСЕР ПЛЕЙЛИСТА PLAYERJS (.txt ФАЙЛОВ) ---
async def fetch_playerjs_playlist(session, pl_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kinovibe.cc/"}
        async with session.get(pl_url, headers=headers, timeout=8) as resp:
            text = await resp.text()
            
            # Если файл зашифрован Base64
            if not text.strip().startswith('[') and not text.strip().startswith('{'):
                cleaned = re.sub(r'^#[0-9]', '', text.strip())
                text = base64.b64decode(cleaned).decode('utf-8', errors='ignore')

            data = json.loads(text)
            
            # Извлекаем массив
            raw_list = data.get("playlist", []) if isinstance(data, dict) else data
            
            playlist = []
            for item in raw_list:
                if isinstance(item, dict) and "file" in item:
                    # Чистим заголовок серии от тегов <br>
                    raw_comment = item.get("comment", "")
                    clean_title = re.sub(r'<[^>]+>', ' ', raw_comment).strip()
                    if not clean_title:
                        clean_title = f"{len(playlist)+1} Серия"
                        
                    playlist.append({
                        "title": clean_title,
                        "url": item["file"]
                    })
            return playlist
    except Exception as e:
        print(f"[❌] Error reading Playerjs playlist: {e}")
        return []

@sio.event
async def connect(sid, environ):
    room_state["connected_count"] += 1
    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    if room_state["owner_sid"] == sid: room_state["owner_sid"] = None

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
async def switch_episode(sid, data):
    if sid != room_state["owner_sid"]: return
    idx = data.get("index", 0)
    if 0 <= idx < len(room_state["playlist"]):
        room_state["current_ep_index"] = idx
        ep = room_state["playlist"][idx]
        room_state["mode"] = "video"
        room_state["current_url"] = ep["url"]
        
        await sio.emit('player_command', {
            'action': 'update_playlist', 
            'playlist': room_state["playlist"], 
            'currentIndex': idx
        })
        await sio.emit('player_command', {
            'action': 'load_video', 
            'url': ep["url"]
        })

@sio.event
async def extract_magic(sid, data):
    if sid != room_state["owner_sid"]: return
    url = data.get("url", "").strip()
    
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Playerjs Engine v4.0 сканирует тайтл...', 'details': url}, to=sid)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kinovibe.cc/"
        }
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                
                # 1. Ищем файл плейлиста Playerjs (.txt)
                match_txt = re.search(r'file\s*:\s*["\'](https?://kinovibe\.cc/player/pl/[^"\']+\.txt)["\']', html)
                
                if match_txt:
                    txt_url = match_txt.group(1)
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Найден плейлист Playerjs!', 'details': txt_url}, to=sid)
                    
                    playlist = await fetch_playerjs_playlist(session, txt_url)
                    
                    if playlist:
                        room_state["playlist"] = playlist
                        room_state["current_ep_index"] = 0
                        room_state["mode"] = "video"
                        room_state["current_url"] = playlist[0]["url"]
                        
                        await sio.emit('server_log', {'type': 'SUCCESS', 'msg': f'Мгновенно загружено {len(playlist)} серий!', 'details': 'Запускаю 1-ю серию'}, to=sid)
                        
                        await sio.emit('player_command', {
                            'action': 'update_playlist', 
                            'playlist': playlist, 
                            'currentIndex': 0
                        })
                        await sio.emit('player_command', {
                            'action': 'load_video', 
                            'url': playlist[0]["url"]
                        })
                        return
                            
                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Плейлист Playerjs не найден', 'details': 'Проверь ссылку.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка сервера!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)
