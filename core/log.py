import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from datetime import datetime


class ConsoleFormatter(logging.Formatter):
    """Formatter für farbige Konsolenausgabe"""

    LEVEL_MAP = {
        "DEBUG": '\033[94m',  # Blau
        "INFO": '\033[92m',  # Grün
        "WARNING": '\033[93m',  # Gelb
        "ERROR": '\033[91m',  # Rot
        "CRITICAL": '\033[95m',  # Magenta
    }

    RESET = '\033[0m'  # Rücksetzen der Farbe

    def format(self, record):
        # Farbe nur für die Konsole anwenden, nicht für die Datei
        if record.levelname == "DEBUG":
            level = 'D'
        elif record.levelname == "INFO":
            level = 'I'
        elif record.levelname == "WARNING":
            level = 'W'
        elif record.levelname == "ERROR":
            level = 'E'
        elif record.levelname == "CRITICAL":
            level = 'C'
        else:
            level = ' '

        # Falls es sich um die Konsole handelt, die Farbe anwenden
        if record.levelname in self.LEVEL_MAP:
            color = self.LEVEL_MAP.get(record.levelname)
            log_message = f"{color}[{level} {datetime.now().strftime('%y%m%d %H:%M:%S')} log:{record.lineno}]{self.RESET} {record.getMessage()}"
        else:
            log_message = f"[{level} {datetime.now().strftime('%y%m%d %H:%M:%S')} log:{record.lineno}] {record.getMessage()}"

        return log_message


class FileFormatter(logging.Formatter):
    """Formatter für Log-Dateien ohne Farben"""

    def format(self, record):
        level = record.levelname[0]  # Nur den ersten Buchstaben des Levels
        date_str = datetime.now().strftime("%y%m%d %H:%M:%S")  # YYMMDD HH:MM:SS
        filename = os.path.basename(record.pathname)  # Nur den Dateinamen ohne Pfad
        log_location = f"[{filename}/{record.funcName}]: " if record.levelname == "DEBUG" else ""

        return f"[{level} {date_str} log:{record.lineno}] {log_location}{record.getMessage()}"


class Logger:
    """Custom Logger Klasse für eine einheitliche Log-Ausgabe"""

    def __init__(self, log_dir="logs", log_filename="app.log", level=logging.DEBUG):
        # Basisverzeichnis der App bestimmen (Root der Anwendung)
        app_root = os.path.abspath(os.getcwd())  # Root der App, wo das Skript gestartet wird
        log_path = os.path.join(app_root, log_dir)  # Log-Verzeichnis im App-Root

        # Falls das Verzeichnis nicht existiert, erstellen
        if not os.path.exists(log_path):
            os.makedirs(log_path)

        log_file = os.path.join(log_path, log_filename)  # Vollständiger Pfad zur Log-Datei

        self.logger = logging.getLogger('custom_logger')
        self.logger.setLevel(level)

        # Logdatei mit Rotation (ohne Farben)
        file_handler = RotatingFileHandler(log_file, mode='a', maxBytes=5 * 1024 * 1024, backupCount=2,
                                           encoding="utf-8", delay=False)
        file_handler.setFormatter(FileFormatter())
        file_handler.setLevel(level)

        # Konsolenausgabe (mit Farben)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ConsoleFormatter())
        console_handler.setLevel(level)

        # Handler hinzufügen
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def get_logger(self):
        return self.logger
