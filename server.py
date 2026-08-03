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

# --- ПАРСЕР ПЛЕЙЛИСТА PLAYERJS ---
async def fetch_playerjs_playlist(session, pl_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kinovibe.cc/"}
        async with session.get(pl_url, headers=headers, timeout=8) as resp:
            text = await resp.text()
            
            if not text.strip().startswith('[') and not text.strip().startswith('{'):
                cleaned = re.sub(r'^#[0-9]', '', text.strip())
                text = base64.b64decode(cleaned).decode('utf-8', errors='ignore')

            data = json.loads(text)
            raw_list = data.get("playlist", []) if isinstance(data, dict) else data
            
            playlist = []
            for item in raw_list:
                if isinstance(item, dict) and "file" in item:
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
    
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Universal Engine v4.2 сканирует...', 'details': url}, to=sid)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://kinovibe.cc/",
            "X-Requested-With": "XMLHttpRequest"
        }
        async with ClientSession(headers=headers) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                
                # 1. Всеядный поиск любого файла .txt или .json плейлиста (/pl/, /plold/ и др.)
                match_file = re.search(r'file\s*:\s*["\']([^"\'\s]+\.(?:txt|json))["\']', html, re.IGNORECASE)
                
                pl_url = None
                if match_file:
                    found_path = match_file.group(1).strip()
                    if found_path.startswith('http'):
                        pl_url = found_path
                    elif found_path.startswith('//'):
                        pl_url = "https:" + found_path
                    else:
                        pl_url = "https://kinovibe.cc" + (found_path if found_path.startswith('/') else '/' + found_path)
                        
                if pl_url:
                    await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Найден плейлист Playerjs!', 'details': pl_url}, to=sid)
                    playlist = await fetch_playerjs_playlist(session, pl_url)
                    
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

                # 2. ФОЛБЭК: Запрос к JSON API по ID новости
                match_id = re.search(r'(\d+)-[a-zA-Z0-9-]+\.html', url) or re.search(r'data-id=["\'](\d+)["\']', html)
                if match_id:
                    news_id = match_id.group(1)
                    api_url = f"https://kinovibe.cc/engine/ajax/download.php?action=list&id={news_id}"
                    
                    await sio.emit('server_log', {'type': 'WARN', 'msg': 'Пробуем встроенный JSON API...', 'details': api_url}, to=sid)
                    
                    async with session.get(api_url, timeout=10) as api_resp:
                        try:
                            api_data = await api_resp.json()
                            if isinstance(api_data, list) and len(api_data) > 0:
                                api_playlist = []
                                for idx, item in enumerate(api_data):
                                    label = item.get("label", f"{idx+1} Серия")
                                    val = item.get("value", idx)
                                    qual = item.get("quality", [480])[0] if isinstance(item.get("quality"), list) else 480
                                    
                                    link_api = f"https://kinovibe.cc/engine/ajax/download.php?action=link&id={news_id}&value={val}&quality={qual}"
                                    async with session.get(link_api, timeout=5) as link_resp:
                                        try:
                                            link_json = await link_resp.json()
                                            if link_json.get("link"):
                                                api_playlist.append({"title": label, "url": link_json["link"]})
                                        except Exception: pass
                                            
                                if api_playlist:
                                    room_state["playlist"] = api_playlist
                                    room_state["current_ep_index"] = 0
                                    room_state["mode"] = "video"
                                    room_state["current_url"] = api_playlist[0]["url"]
                                    
                                    await sio.emit('player_command', {
                                        'action': 'update_playlist', 
                                        'playlist': api_playlist, 
                                        'currentIndex': 0
                                    })
                                    await sio.emit('player_command', {
                                        'action': 'load_video', 
                                        'url': api_playlist[0]["url"]
                                    })
                                    return
                        except Exception: pass

                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Плейлист не найден', 'details': 'Проверь ссылку.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка сервера!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)
