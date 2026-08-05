import os
import re
import json
import base64
import asyncio
import socketio
from aiohttp import web, ClientSession

sio = socketio.AsyncServer(
    cors_allowed_origins='*',
    ping_timeout=30,
    ping_interval=10
)
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

STATE_FILE = "room_state.json"
COOKIES_FILE = "kinovibe_cookies.json"

ffmpeg_process = None

room_state = {
    "owner_sid": None,
    "mode": "iframe",
    "current_url": "https://kinovibe.cc/",
    "media_title": "",
    "playlist": [],
    "current_ep_index": 0,
    "connected_count": 0,
    "has_kv_cookies": False,
    "tg_streaming": False
}

def load_cookies_from_disk():
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                c_dict = json.load(f)
                room_state["has_kv_cookies"] = True
                print("[🔑] Kinovibe HD Cookies loaded!")
                return c_dict
        except Exception as e:
            print(f"[❌] Error loading cookies: {e}")
    return {}

def save_cookies_to_disk(c_dict):
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(c_dict, f, indent=2)
            room_state["has_kv_cookies"] = True
            print("[💾] Kinovibe HD Cookies saved!")
    except Exception as e:
        print(f"[❌] Error saving cookies: {e}")

def save_state_to_disk():
    try:
        data_to_save = {
            "mode": room_state["mode"],
            "current_url": room_state["current_url"],
            "media_title": room_state["media_title"],
            "playlist": room_state["playlist"],
            "current_ep_index": room_state["current_ep_index"]
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[❌] Error saving state: {e}")

def load_state_from_disk():
    global room_state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                room_state["mode"] = saved.get("mode", "iframe")
                room_state["current_url"] = saved.get("current_url", "https://kinovibe.cc/")
                room_state["media_title"] = saved.get("media_title", "")
                room_state["playlist"] = saved.get("playlist", [])
                room_state["current_ep_index"] = saved.get("current_ep_index", 0)
                print("[💾] Room state restored from disk!")
        except Exception as e:
            print(f"[❌] Error loading state: {e}")

load_state_from_disk()
kv_cookies = load_cookies_from_disk()

def extract_clean_title(html):
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
    if not m:
        m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
    if m:
        raw_t = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        raw_t = re.sub(r'\s*смотреть онлайн.*', '', raw_t, flags=re.IGNORECASE).strip()
        raw_t = re.sub(r'\s*в HD.*', '', raw_t, flags=re.IGNORECASE).strip()
        return raw_t
    return "Кинофильм"

async def proxy_video(request):
    target_url = request.query.get("url")
    if not target_url: return web.Response(status=400, text="Missing url")

    headers = {
        "User-Agent": "Mozilla/5.0",
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

async def fetch_playerjs_playlist(session, pl_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kinovibe.cc/"}
        async with session.get(pl_url, headers=headers, cookies=kv_cookies, timeout=8) as resp:
            raw_bytes = await resp.read()
            
            try:
                text = raw_bytes.decode('utf-8-sig', errors='ignore')
            except Exception:
                text = raw_bytes.decode('cp1251', errors='ignore')

            clean_text = re.sub(r'[\x00-\x1f\x7f-\x9f\ufeff]', '', text).strip()

            if not clean_text.startswith('[') and not clean_text.startswith('{'):
                b64_clean = re.sub(r'^#[0-9a-zA-Z]+', '', clean_text)
                b64_clean = re.sub(r'[\r\n\s]', '', b64_clean)
                try:
                    clean_text = base64.b64decode(b64_clean).decode('utf-8-sig', errors='ignore')
                    clean_text = re.sub(r'[\x00-\x1f\x7f-\x9f\ufeff]', '', clean_text)
                except Exception: pass

            playlist = []

            try:
                data = json.loads(clean_text)
                raw_list = data.get("playlist", []) if isinstance(data, dict) else data
                for item in raw_list:
                    if isinstance(item, dict) and "file" in item:
                        raw_comment = item.get("comment", "")
                        clean_title = re.sub(r'<[^>]+>', ' ', raw_comment)
                        clean_title = re.sub(r'[^\w\s\d\[\]\(\)\-\.]', '', clean_title).strip()
                        if not clean_title:
                            clean_title = f"{len(playlist)+1} Серия"
                        playlist.append({"title": clean_title, "url": item["file"]})
            except Exception as json_err:
                print(f"[⚠️] JSON parse failed: {json_err}")

            if not playlist:
                entries = re.findall(r'"file"\s*:\s*"([^"]+)"', text)
                comments = re.findall(r'"comment"\s*:\s*"([^"]+)"', text)
                
                for idx, file_url in enumerate(entries):
                    title = f"{idx+1} Серия"
                    if idx < len(comments):
                        raw_c = comments[idx]
                        clean_c = re.sub(r'<[^>]+>', ' ', raw_c)
                        clean_c = re.sub(r'[^\w\s\d\[\]\(\)\-\.]', '', clean_c).strip()
                        if clean_c: title = clean_c
                    playlist.append({"title": title, "url": file_url})

            return playlist
    except Exception as e:
        print(f"[❌] Error reading Playerjs playlist: {e}")
        return []

# === 📡 МОДУЛЬ СТРИМА В TELEGRAM (НОЛЬ ЛОГОВ НА ДИСК - DEVNULL) ===
async def monitor_ffmpeg(owner_sid):
    global ffmpeg_process
    if ffmpeg_process:
        ret_code = await ffmpeg_process.wait()
        room_state["tg_streaming"] = False
        await sio.emit('tg_stream_status', {'running': False})
        if ret_code != 0 and ret_code != -9:
            await sio.emit('server_log', {'type': 'WARN', 'msg': f'FFmpeg завершил трансляцию (код {ret_code})'}, to=owner_sid)

@sio.event
async def tg_start_stream(sid, data):
    global ffmpeg_process
    if sid != room_state["owner_sid"]: return

    rtmp_server = data.get("rtmp_server", "").rstrip('/')
    stream_key = data.get("stream_key", "").lstrip('/')
    
    if not rtmp_server or not stream_key: return

    full_rtmp = f"{rtmp_server}/{stream_key}"
    video_url = room_state.get("current_url")

    if not video_url:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Стрим не запущен: сначала выберите фильм!'}, to=sid)
        return

    if ffmpeg_process and ffmpeg_process.returncode is None:
        try: ffmpeg_process.kill()
        except: pass

    cmd = [
        "ffmpeg",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-headers", "Referer: https://kinovibe.cc/\r\nUser-Agent: Mozilla/5.0\r\n",
        "-re",
        "-i", video_url,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-max_muxing_queue_size", "1024",
        "-f", "flv",
        full_rtmp
    ]

    print(f"[📡] Starting Telegram Stream via FFmpeg (Zero Disk Logs)...")
    
    try:
        # 🚫 ВЫВОД В DEVNULL (0 БАЙТ НА ДИСКЕ)
        ffmpeg_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        room_state["tg_streaming"] = True
        await sio.emit('tg_stream_status', {'running': True})
        await sio.emit('server_log', {'type': 'SUCCESS', 'msg': '📡 FFmpeg запустил трансляцию в Telegram (Режим 0 Байт Логов)!', 'details': full_rtmp}, to=sid)
        
        asyncio.create_task(monitor_ffmpeg(sid))
    except Exception as e:
        print(f"[❌] FFmpeg start error: {e}")
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка запуска FFmpeg!', 'details': str(e)}, to=sid)

@sio.event
async def tg_stop_stream(sid):
    global ffmpeg_process
    if sid != room_state["owner_sid"]: return

    if ffmpeg_process and ffmpeg_process.returncode is None:
        try:
            ffmpeg_process.kill()
            print("[📡] Telegram Stream stopped.")
        except Exception as e:
            print(f"[❌] FFmpeg kill error: {e}")

    room_state["tg_streaming"] = False
    await sio.emit('tg_stream_status', {'running': False})
    await sio.emit('server_log', {'type': 'INFO', 'msg': '📡 Стрим в Telegram остановлен.'}, to=sid)

@sio.event
async def connect(sid, environ):
    room_state["connected_count"] += 1
    await sio.emit('room_state', room_state, to=sid)
    if room_state["tg_streaming"]:
        await sio.emit('tg_stream_status', {'running': True}, to=sid)

@sio.event
async def disconnect(sid):
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    if room_state["owner_sid"] == sid: room_state["owner_sid"] = None

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        print(f"[👑] Owner authenticated: {sid}")
        await sio.emit('auth_result', {'success': True}, to=sid)
        
        if room_state["mode"] == "video" and room_state["connected_count"] > 1:
            await sio.emit('request_guest_time_for_owner', skip_sid=sid)
    else:
        print(f"[❌] Owner auth failed: '{pin}' vs '{OWNER_PIN}'")
        await sio.emit('auth_result', {'success': False}, to=sid)

@sio.event
async def guest_time_report(sid, data):
    if room_state["owner_sid"]:
        await sio.emit('apply_owner_catchup', data, to=room_state['owner_sid'])

@sio.event
async def kinovibe_login(sid, data):
    global kv_cookies
    if sid != room_state["owner_sid"]: return
    
    username = data.get("login")
    password = data.get("password")
    
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://kinovibe.cc/"}
    payload = {
        "login_name": username,
        "login_password": password,
        "login": "submit"
    }
    
    try:
        async with ClientSession() as session:
            async with session.post("https://kinovibe.cc/index.php?subaction=dologin", data=payload, headers=headers) as resp:
                cookies_dict = {}
                for cookie in session.cookie_jar:
                    cookies_dict[cookie.key] = cookie.value
                
                if "dle_user_id" in cookies_dict or "dle_password" in cookies_dict:
                    kv_cookies = cookies_dict
                    save_cookies_to_disk(cookies_dict)
                    await sio.emit('kinovibe_auth_result', {'success': True, 'msg': 'Успешный вход! HD аккаунт активен.'}, to=sid)
                else:
                    await sio.emit('kinovibe_auth_result', {'success': False, 'msg': 'Неверный логин или пароль Kinovibe!'}, to=sid)
    except Exception as e:
        await sio.emit('kinovibe_auth_result', {'success': False, 'msg': f'Ошибка сети: {str(e)}'}, to=sid)

@sio.event
async def trigger_countdown(sid):
    if sid == room_state["owner_sid"]:
        await sio.emit('start_countdown')

@sio.event
async def guest_ready_sync(sid):
    if room_state["owner_sid"]:
        await sio.emit('request_owner_time', {'guest_sid': sid}, to=room_state["owner_sid"])

@sio.event
async def owner_time_response(sid, data):
    if sid == room_state["owner_sid"]:
        guest_sid = data.get("guest_sid")
        if guest_sid:
            await sio.emit('apply_sync_late', data, to=guest_sid)

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
    save_state_to_disk()
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
        
        save_state_to_disk()
        
        await sio.emit('player_command', {
            'action': 'update_playlist', 
            'playlist': room_state["playlist"], 
            'currentIndex': idx,
            'media_title': room_state["media_title"]
        })
        await sio.emit('player_command', {
            'action': 'load_video', 
            'url': ep["url"]
        })

@sio.event
async def extract_magic(sid, data):
    if sid != room_state["owner_sid"]: return
    url = data.get("url", "").strip()
    
    await sio.emit('server_log', {'type': 'INFO', 'msg': 'Zero-Disk Logs v7.3 сканирует...', 'details': url}, to=sid)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://kinovibe.cc/"
        }

        async with ClientSession(headers=headers, cookies=kv_cookies) as session:
            async with session.get(url, timeout=12) as resp:
                html = await resp.text()
                
                media_title = extract_clean_title(html)
                room_state["media_title"] = media_title
                
                match_file = re.search(r'file\s*:\s*["\']([^"\'\s]+)["\']', html, re.IGNORECASE)
                
                if match_file:
                    found_path = match_file.group(1).strip()
                    
                    if ".txt" in found_path or ".json" in found_path:
                        pl_url = found_path if found_path.startswith('http') else ("https:" + found_path if found_path.startswith('//') else "https://kinovibe.cc" + (found_path if found_path.startswith('/') else '/' + found_path))
                        
                        await sio.emit('server_log', {'type': 'SUCCESS', 'msg': 'Найден плейлист!', 'details': pl_url}, to=sid)
                        playlist = await fetch_playerjs_playlist(session, pl_url)
                        
                        if playlist:
                            room_state["playlist"] = playlist
                            room_state["current_ep_index"] = 0
                            room_state["mode"] = "video"
                            room_state["current_url"] = playlist[0]["url"]
                            
                            save_state_to_disk()
                            
                            await sio.emit('server_log', {'type': 'SUCCESS', 'msg': f'Загружено серий: {len(playlist)}!', 'details': f'Запускаю: {media_title}'}, to=sid)
                            
                            await sio.emit('player_command', {
                                'action': 'update_playlist', 
                                'playlist': playlist, 
                                'currentIndex': 0,
                                'media_title': media_title
                            })
                            await sio.emit('player_command', {
                                'action': 'load_video', 
                                'url': playlist[0]["url"]
                            })
                            return

                    elif ".mp4" in found_path or ".m3u8" in found_path:
                        movie_url = found_path if found_path.startswith('http') else ("https:" + found_path if found_path.startswith('//') else "https://kinovibe.cc" + (found_path if found_path.startswith('/') else '/' + found_path))
                        
                        movie_playlist = [{"title": "Фильм (Смотреть)", "url": movie_url}]
                        room_state["playlist"] = movie_playlist
                        room_state["current_ep_index"] = 0
                        room_state["mode"] = "video"
                        room_state["current_url"] = movie_url
                        
                        save_state_to_disk()
                        
                        await sio.emit('server_log', {'type': 'SUCCESS', 'msg': f'Найден Фильм: {media_title}', 'details': movie_url}, to=sid)
                        
                        await sio.emit('player_command', {
                            'action': 'update_playlist', 
                            'playlist': movie_playlist, 
                            'currentIndex': 0,
                            'media_title': media_title
                        })
                        await sio.emit('player_command', {
                            'action': 'load_video', 
                            'url': movie_url
                        })
                        return

                await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Медиапоток не найден', 'details': 'Проверь ссылку.'}, to=sid)

    except Exception as e:
        await sio.emit('server_log', {'type': 'ERROR', 'msg': 'Ошибка сервера!', 'details': str(e)}, to=sid)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)
