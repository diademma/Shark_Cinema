// === МОДУЛЬ ТРАНСЛЯЦИИ В TELEGRAM (TG_STREAM.JS v2.0) ===
(function() {
    console.log("📡 Загружен модуль Telegram Stream v2.0 (YouTube & Kinovibe)");

    // Загрузка сохраненных ключей из памяти
    const savedServer = localStorage.getItem('tg_rtmp_server') || "rtmp://dc1.rtmp.t.me/s/";
    const savedKey = localStorage.getItem('tg_stream_key') || "";

    // Создаем HTML панель Telegram Стрима
    function createTgPanel() {
        const ownerPanel = document.getElementById('ownerPanel');
        if (!ownerPanel || document.getElementById('tgStreamBox')) return; // Защита от дубликатов

        const tgBox = document.createElement('div');
        tgBox.id = 'tgStreamBox';
        tgBox.style.cssText = 'margin-top: 15px; border-top: 1px solid #332252; padding-top: 12px;';
        
        tgBox.innerHTML = `
            <small style="color: #c77dff; display: block; margin-bottom: 8px; font-weight: bold;">📡 Прямой Стрим в Telegram Канал (Kinovibe / YouTube RTMP):</small>
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px;">
                <input type="text" id="tgRtmpServer" class="url-input" placeholder="Сервер RTMP" value="${savedServer}">
                <input type="password" id="tgStreamKey" class="url-input" placeholder="Ключ трансляции" value="${savedKey}">
            </div>
            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                <button class="btn btn-action btn-magic" style="flex:1;" id="btnTgStart" onclick="window.startTgStream()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    Запустить Стрим в ТГ
                </button>
                <button class="btn btn-action" style="flex:1; background: #ff4757; border:none; display:none;" id="btnTgStop" onclick="window.stopTgStream()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="6" y="6" width="12" height="12"/></svg>
                    Остановить Стрим
                </button>
            </div>
            <span id="tgStatusText" style="font-size: 11px; color: #2ed573; display: none; margin-top: 8px; font-weight: bold;">🟢 Прямой эфир в Telegram запущен (-c copy)!</span>
        `;

        ownerPanel.appendChild(tgBox);
    }

    window.startTgStream = function() {
        const server = document.getElementById('tgRtmpServer').value.trim();
        const key = document.getElementById('tgStreamKey').value.trim();

        if (!server || !key) {
            return alert('Введи Сервер RTMP и Ключ Трансляции из Telegram!');
        }

        // Сохраняем ключи в память планшета
        localStorage.setItem('tg_rtmp_server', server);
        localStorage.setItem('tg_stream_key', key);

        addLog('INFO', 'Запуск FFmpeg стрима в Telegram...', `${server} + Key`);
        
        socket.emit('tg_start_stream', {
            rtmp_server: server,
            stream_key: key
        });
    };

    window.stopTgStream = function() {
        addLog('WARN', 'Остановка стрима в Telegram...');
        socket.emit('tg_stop_stream');
    };

    // Слушаем ответы сервера
    socket.on('tg_stream_status', (data) => {
        const btnStart = document.getElementById('btnTgStart');
        const btnStop = document.getElementById('btnTgStop');
        const statusText = document.getElementById('tgStatusText');

        if (data.running) {
            if (btnStart) btnStart.style.display = 'none';
            if (btnStop) btnStop.style.display = 'inline-flex';
            if (statusText) statusText.style.display = 'block';
            addLog('SUCCESS', '🎉 Трансляция в Telegram успешно запущена!');
        } else {
            if (btnStart) btnStart.style.display = 'inline-flex';
            if (btnStop) btnStop.style.display = 'none';
            if (statusText) statusText.style.display = 'none';
            addLog('INFO', 'Трансляция в Telegram остановлена.');
        }
    });

    // Инициализация панели при любой скорости загрузки страницы
    if (document.readyState === "complete" || document.readyState === "interactive") {
        setTimeout(createTgPanel, 500);
    } else {
        document.addEventListener('DOMContentLoaded', () => {
            setTimeout(createTgPanel, 500);
        });
    }
})();
