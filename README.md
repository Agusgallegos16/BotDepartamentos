# 🏢 Bot Inmobiliario de Departamentos (CABA)

Bot automatizado y liviano para rastrear alquileres tradicionales en Buenos Aires (Zonaprop, Mercado Libre Inmuebles, Remax, Argenprop) con persistencia en SQLite, detección de rebajas de precios y alertas directas a Telegram.

---

## 🚀 Características Principales

* **Ultra Liviano (HTTP Puro)**: Utiliza `curl_cffi` con emulación de TLS/JA3 de Chrome moderno + `selectolax` (motor Lexbor en C). Consume menos de **45 MB de RAM** (sin navegadores pesados tipo Selenium o Playwright).
* **Manejo Robusto de Excepciones (Fail-Soft)**: Si un portal presenta bloqueos de Cloudflare, DataDome o AWS WAF, el error se aísla y el pipeline continúa extrayendo del resto sin detenerse.
* **Control de Duplicados e Histórico en SQLite**: Deduplicación por ID único (`{portal}_{portal_id}`) y tabla `price_history` para detectar **rebajas de precio** en publicaciones existentes.
* **Alertas Enriquecidas en Telegram**:
  * Formato HTML nativo con escape de caracteres seguros.
  * Envío con foto de portada y desglose de precio: `Alquiler + Expensas = Costo Total`.
  * Detección de balcón mediante análisis regex en títulos y descripciones.
* **Soporte Multiplataforma**: Funciona mediante ejecución simple (`python run.py`), bucle programado (`--loop`) o a través de **Cronjob** (Linux) / **Programador de Tareas** (Windows).

---

## 📁 Estructura del Proyecto

```text
BotDepartamentos/
├── data/
│   └── apartments.db         # Base de datos SQLite (se genera automáticamente)
├── logs/
│   └── bot.log               # Registro histórico de ejecuciones
├── src/
│   ├── config.py             # Configuración, dataclasses y mapeo de barrios
│   ├── database.py           # Gestor SQLite y control de rebajas
│   ├── notifier.py           # Notificador de Telegram (parse_mode="HTML")
│   ├── scrapers/
│   │   ├── base.py           # Clase base con TLS impersonation y utilidades
│   │   ├── argenprop.py      # Extractor de Argenprop
│   │   ├── zonaprop.py       # Extractor de Zonaprop
│   │   ├── mercadolibre.py   # Extractor de Mercado Libre Inmuebles
│   │   └── remax.py          # Extractor de Remax (REST API directa)
│   └── pipeline.py           # Orquestador del flujo
├── .env.example              # Plantilla de variables de entorno
├── requirements.txt          # Dependencias exactas
└── run.py                    # Entry point ejecutable
```

---

## ⚙️ Instalación y Configuración

### 1. Clonar o ingresar a la carpeta
```bash
cd c:\Users\agusg\Desktop\BotDepartamentos
```

### 2. Activar el entorno virtual e instalar dependencias
```bash
# En Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configurar variables de entorno
Copiá el archivo `.env.example` como `.env`:
```bash
cp .env.example .env
```

Editá el archivo `.env` con tus credenciales y preferencias:
```env
# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789

# Filtros de búsqueda
MAX_TOTAL_BUDGET_ARS=1100000
MIN_ROOMS=2
TARGET_NEIGHBORHOODS=palermo,caballito,villa-urquiza,villa-del-parque,chacarita,belgrano,recoleta,colegiales,villa-crespo

# Portales activos
ACTIVE_PORTALS=zonaprop,mercadolibre,argenprop
```

> **¿Cómo obtener tu Token y Chat ID de Telegram?**
> 1. Creá un bot hablando con [@BotFather](https://t.me/BotFather) en Telegram (enviá `/newbot`) y copiá el Token.
> 2. Iniciá conversación con tu nuevo bot (apretá "Iniciar").
> 3. Hablá con [@userinfobot](https://t.me/userinfobot) para obtener tu `Id` numérico de chat.

---

## 🧪 Pruebas y Modos de Uso

### Probar conexión a Telegram
Verificá que tus credenciales funcionen antes de correr la búsqueda:
```bash
python run.py --test-telegram
```

### Ejecución manual (una sola corrida)
Ideal para verificar qué departamentos están disponibles en este momento:
```bash
python run.py
```
*(Si no tenés configurado Telegram, imprimirá las alertas simuladas por consola en modo mock).*

### Ejecución en bucle continuo (sin cron externo)
Podés dejarlo corriendo en una consola con un intervalo personalizado en segundos (por defecto 3600 = 1 hora):
```bash
python run.py --loop --interval 3600
```

---

## ⏰ Configuración de Cronjob y Programador de Tareas

### Opción A: Programador de Tareas de Windows (Task Scheduler)
1. Abrí **Programador de tareas** (`taskschd.msc`).
2. Creá una tarea básica llamada `Bot Departamentos`.
3. Desencadenador: **Diariamente**, repetir cada **1 hora**.
4. Acción: **Iniciar un programa**:
   * **Programa o script**: `C:\Users\agusg\Desktop\BotDepartamentos\.venv\Scripts\python.exe`
   * **Argumentos**: `run.py`
   * **Iniciar en**: `C:\Users\agusg\Desktop\BotDepartamentos`

### Opción B: Cronjob en Linux
Editá tu crontab con `crontab -e`:
```bash
# Ejecutar cada hora en punto
0 * * * * cd /ruta/al/proyecto && ./.venv/bin/python run.py >> logs/cron.log 2>&1
```
