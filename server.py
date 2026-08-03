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
    "playlist": [],
    "current_ep_index": 0,
    "connected_count": 0
}

# --- ВИДЕО ТУННЕЛЬ С ПОДДЕРЖКОЙ ПЕРЕМОТКИ ---
async def proxy_video(request):
    target_url = request.query.get("url")
    if not target_url: return web.Response(status=400, text="Missing url")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://kinovibe.cc/",
        "Origin": "https://kinovibe.cc"
    }

    range_header = request.headers.get("Range")
    if range_header: headers["Range"] = range_header

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
                proxy_resp.headers = {k: v for k, v in proxy_resp.headers.items() if v}
                await proxy_resp.prepare(request)
                
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    await proxy_resp.write(chunk)

                await proxy_resp.write_eof()
                return proxy_resp
    except Exception as e:
        return web.Response(status=500, text=str(e))

app.router.add_get('/proxy_video', proxy_video)

# --- АВТОМАТИЧЕСКИЙ СБОРЩИК ВСЕХ СЕРИЙ С КИНОВАЙБА ---
def parse_kinovibe_playlist(html):
    playlist = []
    
    # 1. Находим все ссылки на mp4/m3u8 на странице
    raw_urls = re.findall(r'https?://[^\s"\'<>]+(?:\.mp4|\.m3u8)[^\s"\'<>]*', html)
    clean_urls = [u for u in raw_urls if "/trailer/" not in u and "trailer" not in u.lower()]

    episodes_dict = {}
    
    for url in clean_urls:
        # Ищем номер серии из URL (например s01e03 или s01e3 или e03)
        match = re.search(r's\d+e(\d+)', url, re.IGNORECASE)
        if match:
            ep_num = int(match.group(1))
            if ep_num not in episodes_dict:
                episodes_dict[ep_num] = {"title": f"{ep_num} Серия", "url": url}
            # Если есть выбор качества, берем 720p предпочтительнее 480p
            if "720" in url:
                episodes_dict[ep_num]["url"] = url

    if episodes_dict:
        for ep_num in sorted(episodes_dict.keys()):
            playlist.append(episodes_dict[ep_num])
        return playlist

    # 2. Фолбэк: если это фильм (одна серия)
    unique_urls = list(dict.fromkeys(clean_urls))
    for idx, url in enumerate(unique_urls, 1):
        playlist.append({"title": f"Фильм / Часть {idx}", "url": url})

    return playlist

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

# Переключение серий Овнером
@sio.event
async def switch_episode(sid, data):
    if sid != room_state["owner_sid"]: return
    
    idx = data.get("index", 0)
    if 0 <= idx < len(room_state["playlist"]):
        room_state["current_ep_index"] = idx
        ep = room_state["playlist"][idx]
        room_state["mode"] = "video"
        room_state["current_url"] = ep["url"]
        
        print(f"[🎬] Switching to Episode {idx+1}: {ep['url']}")
        
        # Обновляем состояние у ВСЕХ
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
    
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Парсинг страницы сериала v3.0...', 'details': url}, to=sid)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://kinovibe.cc/"
        }
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                playlist = parse_kinovibe_playlist(html)
                
                if playlist:
                    room_state["playlist"] = playlist
                    room_state["current_ep_index"] = 0
                    room_state["mode"] = "video"
                    room_state["current_url"] = playlist[0]["url"]
                    
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': f'Успешно собрано серий: {len(playlist)}!', 'details': 'Запускаю Серию 1'}, to=sid)
                    
                    # Отправляем плейлист и запускаем первую серию у всех
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
                            
                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Серии не найдены', 'details': 'Проверь ссылку.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка парсера!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)
