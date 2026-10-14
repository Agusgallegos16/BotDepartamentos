from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load .env file
load_dotenv(BASE_DIR / ".env")


@dataclass
class Listing:
    """Represents a standardized real estate listing across all portals."""
    id: str                         # Unificado: {portal}_{portal_id}
    portal: str                     # 'argenprop', 'zonaprop', 'mercadolibre'
    portal_id: str
    url: str
    title: str
    neighborhood: str
    address: str = ""
    price_ars: float = 0.0
    expenses_ars: float = 0.0
    total_cost_ars: float = 0.0     # price_ars + expenses_ars
    rooms: int = 0
    m2: float = 0.0
    has_balcony: bool = False
    thumbnail_url: str = ""

    def __post_init__(self):
        # Asegurar cálculo de costo total
        if self.total_cost_ars <= 0.0:
            self.total_cost_ars = self.price_ars + (self.expenses_ars or 0.0)


# Persistence
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "apartments.db"))

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Search and Business Rules
MAX_TOTAL_BUDGET_ARS = float(os.getenv("MAX_TOTAL_BUDGET_ARS", "1100000"))
MIN_ROOMS = int(os.getenv("MIN_ROOMS", "2"))

_raw_neighborhoods = os.getenv(
    "TARGET_NEIGHBORHOODS",
    "palermo,caballito,villa-urquiza,villa-del-parque,chacarita,belgrano,recoleta,colegiales,villa-crespo",
)
TARGET_NEIGHBORHOODS: List[str] = [
    n.strip().lower() for n in _raw_neighborhoods.split(",") if n.strip()
]

# Scraping settings
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
DELAY_BETWEEN_REQUESTS = float(os.getenv("DELAY_BETWEEN_REQUESTS", "2.0"))

_raw_portals = os.getenv(
    "ACTIVE_PORTALS", "zonaprop,mercadolibre,remax,argenprop,mob,cabaprop,directoaldueno,roomix"
)
ACTIVE_PORTALS: List[str] = [
    p.strip().lower() for p in _raw_portals.split(",") if p.strip()
]

# Slug mappings for each portal
NEIGHBORHOOD_SLUGS = {
    "palermo": {
        "name": "Palermo",
        "argenprop": "palermo",
        "zonaprop": "palermo",
        "mercadolibre": "palermo-capital-federal",
    },
    "caballito": {
        "name": "Caballito",
        "argenprop": "caballito",
        "zonaprop": "caballito",
        "mercadolibre": "caballito-capital-federal",
    },
    "villa-urquiza": {
        "name": "Villa Urquiza",
        "argenprop": "villa-urquiza",
        "zonaprop": "villa-urquiza",
        "mercadolibre": "villa-urquiza-capital-federal",
    },
    "villa-del-parque": {
        "name": "Villa del Parque",
        "argenprop": "villa-del-parque",
        "zonaprop": "villa-del-parque",
        "mercadolibre": "villa-del-parque-capital-federal",
    },
    "chacarita": {
        "name": "Chacarita",
        "argenprop": "chacarita",
        "zonaprop": "chacarita",
        "mercadolibre": "chacarita-capital-federal",
    },
    "belgrano": {
        "name": "Belgrano",
        "argenprop": "belgrano",
        "zonaprop": "belgrano",
        "mercadolibre": "belgrano-capital-federal",
    },
    "recoleta": {
        "name": "Recoleta",
        "argenprop": "recoleta",
        "zonaprop": "recoleta",
        "mercadolibre": "recoleta-capital-federal",
    },
    "colegiales": {
        "name": "Colegiales",
        "argenprop": "colegiales",
        "zonaprop": "colegiales",
        "mercadolibre": "colegiales-capital-federal",
    },
    "villa-crespo": {
        "name": "Villa Crespo",
        "argenprop": "villa-crespo",
        "zonaprop": "villa-crespo",
        "mercadolibre": "villa-crespo-capital-federal",
    },
}
