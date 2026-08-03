import os
import asyncio
import socketio
from aiohttp import web

sio = socketio.AsyncServer(cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

OWNER_PIN = os.getenv("OWNER_PIN", "18349276")

room_state = {
    "owner_sid": None,
    "current_url": "https://kinovibe.cc/",
    "scroll_y": 0,
    "connected_count": 0
}

sleep_timer_task = None

async def auto_sleep_timer():
    """Таймер: через 30 минут отсутствия людей очищает память комнаты"""
    print("[⏳] Все ушли с сайта. Запущен 30-минутный таймер сна...")
    await asyncio.sleep(1800) # 1800 секунд = 30 минут
    
    # Если за 30 минут никто не вернулся
    room_state["current_url"] = "https://kinovibe.cc/"
    room_state["scroll_y"] = 0
    room_state["owner_sid"] = None
    print("[💤] 30 минут прошло! Память комнаты очищена, сервер перешел в режим глубокого сна.")

@sio.event
async def connect(sid, environ):
    global sleep_timer_task
    room_state["connected_count"] += 1
    print(f"[+] Подключился пользователь ({sid}). Всего людей: {room_state['connected_count']}")
    
    # Если кто-то зашел — отменяем таймер сна!
    if sleep_timer_task and not sleep_timer_task.done():
        sleep_timer_task.cancel()
        print("[☀️] Кто-то зашел на сайт! Таймер сна отменен.")

    await sio.emit('room_state', room_state, to=sid)

@sio.event
async def disconnect(sid):
    global sleep_timer_task
    room_state["connected_count"] = max(0, room_state["connected_count"] - 1)
    print(f"[-] Пользователь вышел ({sid}). Осталось людей: {room_state['connected_count']}")

    if room_state["owner_sid"] == sid:
        room_state["owner_sid"] = None
        print("[!] Овнер вышел из комнаты!")

    # Если на сайте осталось 0 человек — запускаем 30-минутный таймер сна
    if room_state["connected_count"] == 0:
        sleep_timer_task = asyncio.create_task(auto_sleep_timer())

@sio.event
async def auth_owner(sid, data):
    pin = str(data.get("pin", "")).strip()
    if pin == OWNER_PIN:
        room_state["owner_sid"] = sid
        print(f"[👑] ОВНЕР АВТОРИЗОВАН: {sid}")
        await sio.emit('auth_result', {'success': True}, to=sid)
    else:
        await sio.emit('auth_result', {'success': False, 'message': 'Неверный ПИН!'}, to=sid)

@sio.event
async def sync_action(sid, data):
    if sid != room_state["owner_sid"]:
        return

    action = data.get("action")
    if action == "load":
        url = data.get("url")
        room_state["current_url"] = url
        room_state["scroll_y"] = 0
        await sio.emit('sync_event', {'action': 'load', 'url': url}, skip_sid=sid)
    elif action == "scroll":
        scroll_y = data.get("y", 0)
        room_state["scroll_y"] = scroll_y
        await sio.emit('sync_event', {'action': 'scroll', 'y': scroll_y}, skip_sid=sid)

if __name__ == '__main__':
    print(f"[READY] Сервер с 30-минутным таймером сна запущен! Активный ПИН: {OWNER_PIN}")
    web.run_app(app, host='0.0.0.0', port=8000)
