# Радио

Радио с двумя ручками: одна выбирает город, другая эпоху, переключение идёт
через шум. Станции «в эфире»: вернёшься — песня ушла вперёд.

Модель корпуса: `Radio.stl`. Исходник:
<https://cad.onshape.com/documents/3f97c19d22cdfbb1ff89ef73/w/63b62facf99d46d81c1500a9/e/cb74c49569cd1aa5f598bcb6>

## Города

Лондон, Париж, Берлин, Рим, Краков, Москва, Нью-Йорк, Лос-Анджелес, Токио,
Тель-Авив, Буэнос-Айрес, Гавана, Дели

## Скачать музыку

[Поставь uv](https://docs.astral.sh/uv/getting-started/installation/), затем:

``` sh
uvx --with curl_cffi yt-dlp --cookies-from-browser chrome --js-runtimes node --remote-components ejs:github --no-warnings https://youtu.be/-bWo0ky8xAs
```

`chrome` замени на свой браузер.

## Установка

``` sh
brew install mpd ffmpeg mp3gain uv # Mac
sudo apt install mpd ffmpeg && curl -LsSf https://astral.sh/uv/install.sh | sh # Raspberry Pi
sudo systemctl disable --now mpd mpd.socket # Pi: штатный mpd держит звуковую карту
cp .env.example .env
```

## Музыка

``` text
music/
  01_Paris/
    1_30s/*.mp3
    2_60s/*.mp3
    3_90s/*.mp3
  02_London/
    ...
static.mp3
```

- Порядок — по имени папки, ставь числовые префиксы.
- Ручка эпох идёт по всем эпохам всех городов; где эпохи нет — шум. Папки без
  MP3 игнорируются.
- Из `static.mp3` собирается петля `static.flac`; для нового клипа поправь точки
  обрезки в `Makefile`.
- `make normalize` выравнивает громкость (нужен `mp3gain`).

## Запуск

``` sh
make local
```

- Клавиатура: ←/→ или A/D — город, ↑/↓ или W/S — эпоха, T — случайное блуждание.
  На краях ручки упираются.
- На Pi — энкодеры KY-040: CLK/DT на BCM-пины из `.env`, `+` на 3.3V, GND на
  GND.
- Код и конфиг одни для Mac и Pi, аудиовыход определяется сам.
- mpd живёт после выхода и перезапускается при старте. Остановить:
  `pkill -f radio-mpd.conf`.

## Подсветка шкалы

Адресная лента на ESP32 с WLED, подключена к Pi (или Mac) по USB, без WiFi.

- WLED: Config → Sync Interfaces → Serial baud `115200`; LED Preferences → тип
  `WS281x` (RGB, не RGBW, иначе светодиоды съезжают), длина покрывает
  максимальный индекс из `.env`, ограничитель яркости 500 mA (питание от USB
  Pi), «Turn LEDs on after power up» выкл., boot preset `0`; WiFi Setup → AP
  opens `Never`, чтобы не светить точкой доступа.
- Порт: `ls /dev/cu.*` (Mac) или `ls /dev/serial/by-id/` (Pi) → `WLED_PORT`.
  Путь by-id переживает переподключение.
- Pi нужен блок 5.1V 2.5A: при просадке
  (`cat /sys/class/hwmon/hwmon1/in0_lcrit_alarm` = `1`) ESP32 циклически
  перезагружается и порт пропадает.
- На Pi пользователь должен быть в группе `dialout`:
  `sudo usermod -aG dialout $USER` и перелогиниться. Без порта, прав или ответа
  WLED радио не стартует.

## Охлаждение

Вентилятор 5V, 2 провода (30 или 40 мм) на GPIO Pi: красный на пин 4 (5V),
чёрный на пин 6 (GND), крутится всегда. Ставь на стенку корпуса, чтобы дул на
радиатор, с выходными дырками напротив; вход закрой сеткой от пыли.

`make healthcheck` — температура, троттлинг и просадки, сейчас и с загрузки.

## Шкала

`make radio.pdf` — шкала для печати (60×20 см + 3 мм под обрез) из `scale.py`;
`make radio.png` — превью. Собирать на Mac: нужен Helvetica Neue.

## Настройки (`.env`)

| Ключ | Что это |
|:---|:---|
| `PREVENT_SLEEP` | `1` — не давать машине уснуть |
| `LOG_LEVEL` | `INFO`, или `DEBUG` — ещё и все команды MPD |
| `WALK_IDLE` | секунд бездействия до случайного блуждания: станция 10–20 с, шум 2–7 с, пока не тронут ручку или клавишу |
| `STATIC_FADE` | секунд кроссфейда между музыкой и шумом |
| `STATIC_HOLD` | секунд чистого шума после поворота, \> 0; станция включается через `STATIC_FADE + STATIC_HOLD` |
| `ENC_LOC_A/B`, `ENC_EPOCH_A/B` | пины CLK/DT энкодеров (только Pi) |
| `WLED_PORT` | серийный порт WLED, пусто — без подсветки |
| `LED_LOCATIONS`, `LED_EPOCHS` | первый светодиод каждого диапазона, по одному на город и эпоху, напр. `0,5,10` |
| `LED_LOCATIONS_LEN`, `LED_EPOCHS_LEN` | светодиодов на город / эпоху |
| `LED_ON`, `LED_OFF` | цвета `RRGGBB` выбранной позиции и остальных |

## Логи

- В терминал.
- Syslog по UDP 514 широковещательно в локальную сеть. На сервере скопируй
  `rsyslog-radio.conf` в `/etc/rsyslog.d/` и перезапусти rsyslog; логи в
  `/var/log/radio.log`.
- Лог mpd: `/tmp/radio-mpd.log`.

Решения по дизайну: `AGENTS.md`.

## Песни

`songs.json`: город → эпоха (`1_early`, `2_middle`, `3_modern`) → 3–7 песен.
`make download` докачивает недостающие в `music/` (первый результат поиска
YouTube, нужен ffmpeg) и удаляет те, которых больше нет в списке.
