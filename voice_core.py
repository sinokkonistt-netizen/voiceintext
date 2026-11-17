import json
import os
import queue
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from pynput import keyboard, mouse

APP_VERSION = "1.1"
GITHUB_REPO = "sinokkonistt-netizen/voiceintext"
MODELS = ["tiny", "base", "small", "medium", "large-v3"]
WHISPER_RATE = 16000
INITIAL_PROMPT = (
    "Диктовка на русском языке. Иногда используются английские слова: "
    "Python, GitHub, Windows, Telegram, iPhone, Wi-Fi, OK. "
    "Цифры пишутся цифрами: 2026, 15 процентов, 3.5 гигабайта."
)
DUMMY_VK = 0xE8
ESC = "esc"
DEFAULTS = {"device": None, "hold_key": "alt", "toggle_key": "f9", "model": "small"}

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "VoiceInput"
CONFIG_PATH = DATA_DIR / "config.json"
MODEL_DIR = DATA_DIR / "models"
LOG_PATH = DATA_DIR / "error.log"

KEY_ALIASES = {
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
}
MOUSE_BUTTONS = {
    mouse.Button.middle: "mouse_middle",
    mouse.Button.x1: "mouse_x1",
    mouse.Button.x2: "mouse_x2",
}
MOUSE_LABELS = {
    "mouse_middle": "колесо мыши",
    "mouse_x1": "боковая кнопка 1",
    "mouse_x2": "боковая кнопка 2",
}


def key_name(key):
    if isinstance(key, keyboard.Key):
        return KEY_ALIASES.get(key.name, key.name)
    if key.char:
        return key.char.lower()
    return None


def display_name(name):
    return MOUSE_LABELS.get(name, name.upper())


def version_tuple(text):
    return tuple(int(p) for p in text.split(".") if p.isdigit())


def prepare_audio(audio, rate):
    if rate != WHISPER_RATE:
        n = int(len(audio) * WHISPER_RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 1e-4:
        audio = audio * min(0.9 / peak, 30.0)
    return audio.astype(np.float32)


def find_input_devices():
    try:
        api = sd.query_devices(kind="input")["hostapi"]
    except Exception:
        return []
    return [(i, d["name"], int(d["default_samplerate"])) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0 and d["hostapi"] == api]


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def check_update():
    if GITHUB_REPO == "OWNER/REPO":
        return None
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"User-Agent": "VoiceInput"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    latest = data["tag_name"].lstrip("vV")
    if version_tuple(latest) > version_tuple(APP_VERSION):
        return latest, data["html_url"]
    return None


class Engine:
    def __init__(self):
        self.events = queue.Queue()
        saved = load_config()
        if "hotkey" in saved and "hold_key" not in saved:
            saved["hold_key"] = saved["hotkey"]
        self.cfg = {**DEFAULTS, **saved}
        self.devices = find_input_devices()
        if not self.devices:
            raise RuntimeError("Микрофоны не найдены. Подключите микрофон и перезапустите приложение.")
        self.device = next((d for d in self.devices if d[1] == self.cfg["device"]), self.devices[0])
        self.hotkeys = {"hold": self.cfg["hold_key"], "toggle": self.cfg["toggle_key"]}
        self.enabled = False
        self.capturing = None
        self.recording = False
        self.toggle_down = False
        self.frames = []
        self.stream = None
        self.rec_model = None
        self.model = None
        self.model_name = None
        self.load_id = 0
        self.level = 0.0
        self.typer = keyboard.Controller()
        self.key_listener = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
        self.mouse_listener = mouse.Listener(on_click=self._on_mouse_click)

    def start(self):
        self.key_listener.start()
        self.mouse_listener.start()
        self.set_model(self.cfg["model"], save=False)
        threading.Thread(target=self._check_update, daemon=True).start()

    def stop(self):
        self.key_listener.stop()
        self.mouse_listener.stop()

    def set_enabled(self, value):
        self.enabled = value
        self.events.put(("state", value))

    def set_device(self, name):
        self.device = next(d for d in self.devices if d[1] == name)
        self._save("device", name)

    def set_model(self, name, save=True):
        if self.model is not None and name == self.model_name:
            return
        if save:
            self._save("model", name)
        self.load_id += 1
        self.model = None
        threading.Thread(target=self._load_model, args=(name, self.load_id), daemon=True).start()

    def start_capture(self, target):
        self.capturing = target

    def _save(self, key, value):
        self.cfg[key] = value
        save_config(self.cfg)

    def _load_model(self, name, token):
        self.events.put(("status", f"Загрузка модели '{name}'... (при первом запуске нужен интернет)"))
        try:
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
        except Exception as e:
            if token == self.load_id:
                self.events.put(("status", f"Не удалось загрузить модель '{name}': {e}"))
            return
        if token == self.load_id:
            self.model = model
            self.model_name = name
            self.events.put(("model", name))

    def _check_update(self):
        try:
            found = check_update()
        except Exception:
            return
        if found:
            self.events.put(("update", found[0], found[1]))

    def _on_key_press(self, key):
        self._press(key_name(key))

    def _on_key_release(self, key):
        self._release(key_name(key))

    def _on_mouse_click(self, x, y, button, pressed):
        name = MOUSE_BUTTONS.get(button)
        if name is None:
            return
        if pressed:
            self._press(name)
        else:
            self._release(name)

    def _press(self, name):
        if name is None:
            return
        if self.capturing:
            self._finish_capture(name)
            return
        if name == self.hotkeys["toggle"]:
            if not self.toggle_down:
                self.toggle_down = True
                self.set_enabled(not self.enabled)
            return
        if name == self.hotkeys["hold"] and self.enabled and not self.recording:
            self._start_recording()

    def _release(self, name):
        if name == self.hotkeys["toggle"]:
            self.toggle_down = False
        if self.recording and name == self.hotkeys["hold"]:
            self._stop_recording()

    def _finish_capture(self, name):
        target = self.capturing
        self.capturing = None
        if name != ESC:
            other = "toggle" if target == "hold" else "hold"
            if name == self.hotkeys[other]:
                self.events.put(("error", "Эта кнопка уже назначена на другое действие"))
            else:
                self.hotkeys[target] = name
                self._save("hold_key" if target == "hold" else "toggle_key", name)
        self.events.put(("capture", target))

    def _start_recording(self):
        if self.model is None:
            self.events.put(("status", "Модель ещё загружается"))
            return
        self.frames = []
        self.rec_model = self.model
        try:
            self.stream = sd.InputStream(device=self.device[0], samplerate=self.device[2], channels=1,
                                         dtype="float32", callback=self._on_audio)
            self.stream.start()
        except Exception as e:
            self.events.put(("status", f"Ошибка микрофона: {e}"))
            return
        self.recording = True
        self.events.put(("recording", True))

    def _on_audio(self, indata, *_):
        chunk = indata[:, 0].copy()
        self.frames.append(chunk)
        self.level = float(np.sqrt(np.mean(chunk ** 2)))

    def _stop_recording(self):
        self.recording = False
        self.level = 0.0
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        audio = np.concatenate(self.frames) if self.frames else np.zeros(0, dtype=np.float32)
        self.frames = []
        self.events.put(("status", "Распознаю..."))
        threading.Thread(target=self._transcribe, args=(audio, self.device[2], self.rec_model),
                         daemon=True).start()

    def _transcribe(self, audio, rate, model):
        try:
            if len(audio) < rate * 0.3:
                self.events.put(("status", "Запись слишком короткая"))
                return
            segments, _ = model.transcribe(
                prepare_audio(audio, rate),
                language="ru",
                initial_prompt=INITIAL_PROMPT,
                beam_size=5,
                best_of=5,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text:
                self.events.put(("status", "Речь не распознана"))
                return
            self.events.put(("result", text))
            time.sleep(0.15)
            self.typer.press(keyboard.KeyCode.from_vk(DUMMY_VK))
            self.typer.release(keyboard.KeyCode.from_vk(DUMMY_VK))
            time.sleep(0.05)
            self.typer.type(text + " ")
            self.events.put(("status", "Текст введён в активное поле"))
        except Exception as e:
            self.events.put(("status", f"Ошибка распознавания: {e}"))
