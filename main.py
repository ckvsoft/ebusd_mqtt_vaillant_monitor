import re

import eventlet

eventlet.monkey_patch()  # Muss zuerst kommen!

import threading
import paho.mqtt.client as mqtt
from flask import Flask, render_template, send_from_directory, Response
from flask_socketio import SocketIO
from datetime import datetime, timedelta
import time
import json
import os

from core.log import Logger

# Flask App initialisieren
app = Flask(__name__, static_url_path='/js', static_folder='js', template_folder='templates')
socketio = SocketIO(app)
# Logger initialisieren

logger_instance = Logger(log_filename="vaillant2.log")  # Erstelle das Logger-Objekt
LOG_FILE_PATH = logger_instance.get_log_file()  # Hole den Logfile-Pfad direkt von der Logger-Instanz

# Jetzt kannst du den Logger verwenden
log = logger_instance.get_logger()  # Hol dir den eigentlichen Logger

# MQTT Callback-Funktionen
def on_connect(client, _userdata, _flags, reason_code, _properties):
    log.info(f"Verbunden mit Code: {reason_code}")
    for topic in mqtt_values:
        client.subscribe(topic)
        log.info(f"Abonniert: {topic}")

def on_message(_client, _userdata, msg):

    topic = msg.topic
    payload = msg.payload.decode()

    try:
        # Zugriff auf die Konfiguration für dieses Topic
        topic_config = mqtt_values.get(topic)

        if topic_config:
            topic_type = topic_config.get("type")
            title = topic_config.get("title")
            value = topic_config.get("value")

            if topic_type == "text":
                data_type = mqtt_values.get(topic, {}).get("data_type", "string")  # Standard: string
                if data_type == "float":
                    try:
                        formatted_value = "{:.2f}".format(float(payload))
                    except ValueError:
                        formatted_value = "N/A"
                elif data_type == "int":
                    try:
                        formatted_value = str(int(payload))
                    except ValueError:
                        formatted_value = "N/A"
                else:  # Standard: string
                    formatted_value = str(payload)

                if value != formatted_value:
                    topic_config["value"] = formatted_value
                    socketio.emit('update_text', {'title': title, 'value': formatted_value})
                    log.debug(f"Update Text Topic: {topic} value: {formatted_value}")

            elif topic_type == "gauge":
                try:
                    new_value = float(payload)
                except ValueError:
                    new_value = 0

                if value != new_value:
                    topic_config["value"] = new_value
                    is_integer = topic_config.get("isInteger")
                    min_range, max_range = topic_config.get("range", (0, 100))
                    color_ranges = topic_config.get('color_ranges', [])
                    socketio.emit('update_gauge', {
                        'title': title, 'value': new_value,
                        'min_range': min_range, 'max_range': max_range,
                        'isInteger': is_integer,
                        'color_ranges': color_ranges
                    })
                    log.debug(f"Update Gauge Topic: {topic} value: {new_value}")

            elif topic_type == "led":
                status = payload.split(";")[-1].strip()

                if topic_config.get("is_processing", False):
                    log.info(f"Verarbeitung für Topic {topic} läuft bereits.")
                    return

                if value != status:
                    log.debug(f"Topic {topic}, prev value: {value}, curr value {status}.")
                    topic_config["is_processing"] = True  # Sperre für Verarbeitung
                    topic_config["value"] = status

                    # Falls ein direkter Wechsel zwischen "hwc" und "on" passiert
                    if (value == "hwc" and status == "on") or (value == "on" and status == "hwc"):
                        if topic_config.get("start_time"):
                            log.info(f"switch from {value} to {status}")
                            start_time = float(topic_config["start_time"])
                            now = datetime.now()
                            elapsed = (now.timestamp() - start_time) / 3600  # Stunden
                            update_runtime(elapsed)

                            runtime["total"] += elapsed
                            hwc["switch"] = True
                            hwc["sub"] += 1
                            topic_config["start_time"] = ""

                    if status in ["on", "hwc"]:
                        # Zähler nicht hochzählen, wenn ein direkter wechsel war. Das zählt nicht als Kompressor start
                        if not hwc["switch"]:
                            counter["today"] += 1
                            counter["total"] += 1
                            c = counter.get("today")
                            log.info(f"start run {c} - {status}")

                        if status == "hwc":
                            hwc["status"] = True
                        topic_config["start_time"] = str(datetime.now().timestamp())

                    elif status not in ["on", "hwc"]:
                        hwc["switch"] = False
                        if topic_config.get("start_time"):
                            start_time = float(topic_config["start_time"])
                            now = datetime.now()
                            elapsed = (now.timestamp() - start_time) / 3600
                            update_runtime(elapsed)

                            runtime["total"] += elapsed
                            topic_config["start_time"] = ""
                            hwc["sub"] = 0
                            hwc["switch"] = False
                            c = counter.get("today")
                            log.info(f"stop run {c} - {status} - elapsed: {elapsed}")


                    socketio.emit('update_led', {
                        'title': title,
                        'value': status,
                        'start_time': topic_config.get("start_time"),
                    })
                    rt = {
                        "today": format_runtime(runtime.get("today", 0)),
                        "yesterday": format_runtime(runtime.get("yesterday", 0)),
                        "runs_today": format_runs(runtime.get("runs", {}).get("today", {})),
                        "runs_yesterday": format_runs(runtime.get("runs", {}).get("yesterday", {}))
                    }

                    socketio.emit('update_counter', counter)
                    socketio.emit('update_runtime', rt)

                    save_values(counter, "data.json")
                    save_values(runtime, "runtime.json")
                    log.debug(f"Update LED Topic: {topic} status: {status}")
                    topic_config["is_processing"] = False  # Verarbeitung abgeschlossen

            else:
                log.warning(f"Unbekanntes Topic: {topic}")
        else:
            log.warning(f"Kein gültiger Eintrag für das Topic: {topic}")

    except Exception as e:
        log.error(f"Fehler beim Verarbeiten von {topic}: {payload} -> {e}")
        log.error(runtime["runs"])

def update_runtime(elapsed):
    cnt = counter.get("today", 0)
    if cnt == 0 and elapsed > 0.0:
        runtime["yesterday"] += elapsed
        run_id = str(counter.get("yesterday", 1))
        sub_id = hwc.get("sub", 0)
        if sub_id > 0:
            run_id = f"{run_id}.{sub_id}"
        runtime["runs"]["yesterday"][run_id] = f"hwc: {elapsed}" if hwc["status"] else elapsed
    else:
        runtime["today"] += elapsed
        run_id = str(cnt)
        sub_id = hwc.get("sub", 0)
        if sub_id > 0:
            run_id = f"{run_id}.{sub_id}"
        runtime["runs"]["today"][run_id] = f"hwc: {elapsed}" if hwc["status"] else elapsed

    hwc["status"] = False

def save_values(data, filename="data.json"):
    try:
        with open(filename, "w") as file:
            json.dump(data, file, indent=4)
    except Exception as e:
        log.error(f"Fehler beim Speichern: {e}")


def load_config(config_file="config.json", default_file="default_config.json"):
    """Lädt die Konfiguration und ergänzt fehlende Werte mit Standardwerten."""
    try:
        with open(default_file, "r") as file:
            default_config = json.load(file)
    except FileNotFoundError:
        log.warning(f"Warnung: Standardkonfigurationsdatei '{default_file}' nicht gefunden.")
        default_config = {}
    except Exception as e:
        log.error(f"Fehler beim Laden der Standardkonfiguration: {e}")
        default_config = {}

    try:
        with open(config_file, "r") as file:
            user_config = json.load(file)
    except FileNotFoundError:
        log.info(f"Info: Konfigurationsdatei '{config_file}' nicht gefunden. Standardwerte werden verwendet.")
        user_config = {}
    except Exception as e:
        log.error(f"Fehler beim Laden der Konfigurationsdatei: {e}")
        user_config = {}

    # Rekursive Zusammenführung der Konfigurationen
    def merge_dicts(default, override):
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(default.get(key), dict):
                default[key] = merge_dicts(default[key], value)
            else:
                default[key] = value
        return default

    return merge_dicts(default_config, user_config)

def load_values(filename="data.json"):
    """Lädt gespeicherte Werte aus einer JSON-Datei."""
    try:
        with open(filename, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        log.warning(f"Datei {filename} nicht gefunden. Standardwerte werden verwendet.")
        return {}
    except Exception as e:
        log.error(f"Fehler beim Laden der Werte aus {filename}: {e}")
        return None


def reset_counter():
    while True:
        now = datetime.now()
        # Warte bis Mitternacht
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_to_sleep = (next_midnight - now).total_seconds()
        time.sleep(time_to_sleep)

        # Verschiebe "today" zu "yesterday" und setze zurück
        counter["yesterday"] = counter["today"]
        counter["today"] = 0

        runtime["yesterday"] = runtime["today"]
        runtime["today"] = 0.0

        runtime["runs"]["yesterday"] = runtime["runs"]["today"]
        runtime["runs"]["today"] = {}

        # Speichere die Werte
        save_values(counter, "data.json")
        save_values(runtime, "runtime.json")

        rt = {
            "today": format_runtime(runtime.get("today", 0)),
            "yesterday": format_runtime(runtime.get("yesterday", 0)),
            "runs_today": format_runs(runtime.get("runs", {}).get("today", {})),
            "runs_yesterday": format_runs(runtime.get("runs", {}).get("yesterday", {}))
        }

        # Sende aktualisierte Werte an die Webseite
        socketio.emit('update_counter', counter)
        socketio.emit('update_runtime', rt)


def format_log_line(line):
    """Formatierung der Log-Zeile mit Farbzuweisung für den Text in [ ] und Zeilenumbrüchen."""

    line = line.strip()
    # Farbzuweisung für die Log-Level in [ ]
    line = re.sub(r'(\[I[^\]]*\])', r'<span style="color: green;">\1</span>', line)
    line = re.sub(r'(\[D[^\]]*\])', r'<span style="color: blue;">\1</span>', line)
    line = re.sub(r'(\[E[^\]]*\])', r'<span style="color: red;">\1</span>', line)

    # Zeilenumbruch sicherstellen
    return f'{line}<br>'


def read_log_file():
    """Funktion, die das Logfile liest und kontinuierlich neue Zeilen sendet."""
    with open(LOG_FILE_PATH, 'r') as f:
        f.seek(0, os.SEEK_END)  # Setzt den Dateizeiger ans Ende des Logfiles
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)  # Warten auf neue Zeilen
                continue
            formatted_line = format_log_line(line)  # Formatierung der Zeile
            yield f"data: {formatted_line}\n\n"  # Sende die Log-Zeile an den Client

def read_entire_log_file():
    """Funktion, die das gesamte Logfile beim ersten Aufruf liest."""
    with open(LOG_FILE_PATH, 'r') as f:
        for line in f:
            formatted_line = format_log_line(line)  # Formatierung der Zeile
            yield f"data: {formatted_line}\n\n"  # Sende jede Zeile an den Client

@app.route('/update_log')
def update_log():
    """Route, um den Logfile-Stream mit SSE an den Client zu senden."""
    def stream_logs():
        # Das gesamte Logfile senden
        yield from read_entire_log_file()
        # Danach nur neue Logzeilen (wie tail -f)
        yield from read_log_file()
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"  # Bei Verwendung von Nginx
    }
    return Response(stream_logs(), headers=headers)

@app.route('/logger')
def logger():
    return render_template('logs.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/')
def index():
    gauges = []
    texts = []
    leds = []  # Neue Liste für LEDs
    for topic, data in mqtt_values.items():
        title = data["title"]
        value = data["value"]

        # Farbbereiche einlesen, wenn sie vorhanden sind
        color_ranges = data.get("color_ranges", [])

        if data["type"] == "gauge":
            is_integer = data["isInteger"]
            min_range, max_range = data["range"]
            gauges.append({
                "title": title,
                "value": value,
                "min_range": min_range,
                "max_range": max_range,
                "color_ranges": color_ranges,
                "isInteger": is_integer
            })
        elif data["type"] == "text":
            texts.append(f"{title}: {value}")
        elif data["type"] == "led":
            # LEDs zur Liste hinzufügen
            leds.append({
                "title": title,
                "value": value,
                'start_time': str(mqtt_values[topic].get("start_time", ""))
            })

    rt = {
        "today": format_runtime(runtime.get("today", 0)),
        "yesterday": format_runtime(runtime.get("yesterday", 0)),
        "runs_today": format_runs(runtime.get("runs", {}).get("today", {})),
        "runs_yesterday": format_runs(runtime.get("runs", {}).get("yesterday", {})),
    }
    # Übergabe der LEDs an das Template
    return render_template('index.html', gauges=gauges, texts=texts, leds=leds, counter=counter, runtime=rt)


def format_runtime(hours):
    total_minutes = int(hours * 60)
    return f"{total_minutes // 60} Std {total_minutes % 60} Min"


def format_runs(runs):
    formatted_runs = []
    for key, value in runs.items():
        if isinstance(value, str) and value.startswith("hwc:"):
            # Wenn der Wert mit hwc: beginnt, extrahiere die Zeit und füge HWC hinzu
            try:
                elapsed_time = float(value.split(":")[1].strip())
                minutes = int(elapsed_time * 60)
                seconds = int((elapsed_time * 3600) % 60)
                formatted_runs.append(f'{key}: HWC {minutes} Min {seconds:02d} Sek')
            except ValueError:
                formatted_runs.append(f'{key}: Ungültiger Wert')
        else:
            # Normaler Fall, wenn der Wert numerisch ist
            minutes = int(value * 60)
            seconds = int((value * 3600) % 60)
            formatted_runs.append(f'{key}: {minutes} Min {seconds:02d} Sek')

    return ', '.join(formatted_runs)


config = load_config()
mqtt_config = config.get("mqtt_config", {})
mqtt_client = mqtt.Client(protocol=mqtt.MQTTv5)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if mqtt_config.get("username", None) is not None:
    mqtt_client.username_pw_set(mqtt_config.get("username"), mqtt_config.get("password", ""))
mqtt_client.connect(mqtt_config.get("host", "localhost"), mqtt_config.get("port", 1883), 60)

if __name__ == '__main__':
    hwc = {"status": False, "switch": False, "sub": 0}

    # Zähler für "on" und "hwc"
    runtime = load_values("runtime.json")

    if runtime is None or len(runtime) == 0:
        runtime = {
            "today": 0.0,
            "yesterday": 0.0,
            "total": 0.0,
            "runs": {"today": {}, "yesterday": {}}
        }
    else:
        runtime.setdefault("runs", {})
        runtime["runs"].setdefault("today", {})
        runtime["runs"].setdefault("yesterday", {})

    counter = load_values("data.json") or {
        "today": 0,
        "yesterday": 0,
        "total": 0,
    }

    # Zugriff auf die MQTT-Werte mit Typen und anderen Informationen
    mqtt_values = config.get("mqtt_values", {})

    threading.Thread(target=reset_counter, daemon=True).start()
    threading.Thread(target=mqtt_client.loop_forever, daemon=True).start()
    socketio.run(app, debug=False, host='0.0.0.0', port=5000, use_reloader=False, log_output=True)
