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

# --- ПРОВЕРКА ДЕЙСТВИТЕЛЬНОСТИ МЕДИАССЫЛКИ ---
def is_valid_media_link(href, label=""):
    href_lower = href.lower()
    
    # Игнорируем категории, теги, года и трейлеры
    if any(bad in href_lower for bad in ['/trailer/', 'trailer', 'yearanime', '/anime/', '/serial/', '/film/', 'category', 'tags']):
        if not ('download.php' in href_lower or 'kvb.cool' in href_lower or '.mp4' in href_lower or '.m3u8' in href_lower):
            return False
            
    if 'download.php' in href_lower or 'engine/download' in href_lower or 'kvb.cool' in href_lower or '.mp4' in href_lower or '.m3u8' in href_lower:
        return True
        
    if '[480]' in label or '[720]' in label or '[1080]' in label:
        return True
        
    return False

# --- СТРОГИЙ ПАРСЕР СЕРИЙ ---
def parse_omnivorous_playlist(html, base_url="https://kinovibe.cc"):
    playlist = []
    
    # Находим все вхождения "X Серия"
    matches = list(re.finditer(r'(\d+)\s*Серия\s*(\[[^\]]+\])?', html, re.IGNORECASE))
    episodes_map = {}

    for m in matches:
        ep_num = int(m.group(1))
        voice = m.group(2).strip() if m.group(2) else ""
        
        # Сканируем 600 символов ПОСЛЕ надписи "X Серия" в поисках тега <a>
        start_pos = m.end()
        snippet = html[start_pos:start_pos+600]
        
        a_tags = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', snippet, re.IGNORECASE | re.DOTALL)
        
        for href, label in a_tags:
            href_clean = href.strip()
            if href_clean.startswith('//'): href_clean = 'https:' + href_clean
            elif href_clean.startswith('/'): href_clean = base_url.rstrip('/') + href_clean
            
            if is_valid_media_link(href_clean, label):
                title = f"{ep_num} Серия {voice}".strip()
                priority = 2 if '720' in label or '720' in href_clean else 1
                
                if ep_num not in episodes_map or priority > episodes_map[ep_num]['priority']:
                    episodes_map[ep_num] = {
                        "title": title,
                        "url": href_clean,
                        "priority": priority
                    }
                break

    if episodes_map:
        for ep_num in sorted(episodes_map.keys()):
            playlist.append({
                "title": episodes_map[ep_num]["title"],
                "url": episodes_map[ep_num]["url"]
            })
        return playlist

    # Резервный поиск по всем валидным ссылкам на странице
    all_a = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
    seen_urls = set()
    for href, label in all_a:
        href_clean = href.strip()
        if href_clean.startswith('//'): href_clean = 'https:' + href_clean
        elif href_clean.startswith('/'): href_clean = base_url.rstrip('/') + href_clean
        
        if is_valid_media_link(href_clean, label) and href_clean not in seen_urls:
            seen_urls.add(href_clean)
            playlist.append({
                "title": f"Серия {len(playlist)+1}",
                "url": href_clean
            })

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
    
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Строгий сканер v3.3 в работе...', 'details': url}, to=sid)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kinovibe.cc/"
        }
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                playlist = parse_omnivorous_playlist(html)
                
                if playlist:
                    room_state["playlist"] = playlist
                    room_state["current_ep_index"] = 0
                    room_state["mode"] = "video"
                    room_state["current_url"] = playlist[0]["url"]
                    
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': f'Собрано серий/частей: {len(playlist)}!', 'details': 'Запускаю 1-ю серию'}, to=sid)
                    
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
