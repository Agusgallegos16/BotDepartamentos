#!/usr/bin/env python3
"""
BotDepartamentos - Script Principal de Ejecución
Ejecutable manualmente, vía Cronjob (Linux/macOS) o Programador de Tareas (Windows).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Asegurar codificación UTF-8 en consola de Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Asegurar path de importación
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import BASE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.notifier import TelegramNotifier
from src.pipeline import Pipeline


def setup_logging(verbose: bool = False) -> None:
    """Configura el logging para consola y archivo."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # File Handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def test_telegram_connection() -> None:
    """Envía un mensaje de prueba a Telegram para validar tokens."""
    print("\n[PROBANDO CONEXIÓN A TELEGRAM]...")
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "TU_TELEGRAM_BOT_TOKEN_AQUI":
        print("❌ Error: TELEGRAM_BOT_TOKEN no está configurado en .env")
        return
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "TU_TELEGRAM_CHAT_ID_AQUI":
        print("❌ Error: TELEGRAM_CHAT_ID no está configurado en .env")
        return

    import requests

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🤖 <b>¡Bot de Departamentos conectado exitosamente!</b>\n\nLas notificaciones con formato HTML y fotos llegarán aquí cada vez que se detecte una propiedad o rebaja.",
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("✅ ¡Mensaje de prueba enviado exitosamente a tu Telegram!")
        else:
            print(f"❌ Falló el envío ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"❌ Error de red al conectar con Telegram: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bot de búsqueda de departamentos en Buenos Aires."
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Envía una notificación de prueba a Telegram para verificar credenciales.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Ejecuta en bucle continuo en lugar de una sola corrida (ideal para no usar cron externo).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Intervalo en segundos entre corridas cuando se usa --loop (por defecto 3600 = 1 hora).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Activa logs detallados de depuración (DEBUG).",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.test_telegram:
        test_telegram_connection()
        return

    pipeline = Pipeline()

    if args.loop:
        logging.info(
            f"Iniciando modo bucle continuo. Escaneo cada {args.interval} segundos ({args.interval / 60:.1f} min)."
        )
        try:
            while True:
                pipeline.run()
                logging.info(
                    f"Pausando durante {args.interval} segundos hasta el próximo ciclo..."
                )
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logging.info("Detención manual por el usuario (Ctrl+C). Saliendo.")
    else:
        # Modo por defecto: Ejecución única para cronjob / task scheduler
        pipeline.run()


if __name__ == "__main__":
    main()
