from __future__ import annotations

import abc
import logging
import re
import time
from typing import List, Optional
from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from src.config import (
    DELAY_BETWEEN_REQUESTS,
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    REQUEST_TIMEOUT,
    TARGET_NEIGHBORHOODS,
    Listing,
)

logger = logging.getLogger(__name__)


class BaseScraper(abc.ABC):
    """Abstract base class for real estate scrapers."""

    portal_name: str = "base"

    def __init__(self):
        self.session = requests.Session(impersonate="chrome124")
        self.session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )

    def fetch_html(self, url: str) -> Optional[str]:
        """Performs HTTP GET with TLS fingerprint impersonation and error handling."""
        try:
            logger.info(f"[{self.portal_name}] GET {url}")
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code in (403, 429):
                logger.warning(
                    f"[{self.portal_name}] Bloqueo o límite detectado (HTTP {resp.status_code}) en {url}"
                )
                return None
            else:
                logger.warning(
                    f"[{self.portal_name}] Respuesta inesperada HTTP {resp.status_code} en {url}"
                )
                return None
        except Exception as e:
            logger.error(f"[{self.portal_name}] Error en request a {url}: {e}")
            return None

    @staticmethod
    def parse_ars_number(raw_text: str) -> float:
        """Extrae número en pesos argentinos a partir de textos tipo '$ 850.000' o '850.000'."""
        if not raw_text or "USD" in raw_text or "U$S" in raw_text:
            return 0.0
        # Buscar dígitos separados por puntos
        cleaned = re.sub(r"[^\d]", "", raw_text)
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def detect_balcony(text: str) -> bool:
        """Determina mediante regex si el texto hace referencia a balcón o terraza."""
        if not text:
            return False
        pattern = r"(?i)\b(balc[oó]n|aterrazado|terraza|balconete)\b"
        return bool(re.search(pattern, text))

    @staticmethod
    def extract_rooms(text: str) -> int:
        """Extrae la cantidad de ambientes a partir de texto."""
        if not text:
            return 0
        text_lower = text.lower()
        if "monoambiente" in text_lower or "1 amb" in text_lower:
            return 1
        match = re.search(r"(\d+)\s*(?:amb|ambiente|dorm)", text_lower)
        if match:
            return int(match.group(1))
        return 0

    @abc.abstractmethod
    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        """Extrae los avisos correspondientes a un barrio específico."""
        pass

    def scrape_all(self) -> List[Listing]:
        """
        Itera sobre todos los barrios objetivo con fail-soft:
        Si un barrio o el portal falla, no interrumpe el resto del flujo.
        """
        all_listings: List[Listing] = []
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        for nb in TARGET_NEIGHBORHOODS:
            try:
                listings = self.scrape_neighborhood(nb)
                # Filtrado de negocio básico
                valid = []
                for item in listings:
                    # Descartar precios nulos o avisos spam/servicios (menos de $150.000 ARS)
                    if item.price_ars < 150000:
                        logger.debug(
                            f"[{self.portal_name}] Descartado '{item.title}': precio ${item.price_ars:,.0f} menor a $150.000"
                        )
                        continue
                    if item.total_cost_ars > MAX_TOTAL_BUDGET_ARS:
                        logger.debug(
                            f"[{self.portal_name}] Descartado '{item.title}': costo total ${item.total_cost_ars:,.0f} "
                            f"(alquiler ${item.price_ars:,.0f} + exp ${item.expenses_ars:,.0f}) supera ${MAX_TOTAL_BUDGET_ARS:,.0f}"
                        )
                        continue
                    if item.rooms > 0 and item.rooms < MIN_ROOMS:
                        logger.debug(
                            f"[{self.portal_name}] Descartado '{item.title}': {item.rooms} amb menor a mínimo ({MIN_ROOMS} amb)"
                        )
                        continue
                    valid.append(item)

                logger.info(
                    f"[{self.portal_name}] Barrio '{nb}': {len(listings)} encontrados, {len(valid)} cumplen criterios."
                )
                all_listings.extend(valid)
            except Exception as e:
                logger.error(
                    f"[{self.portal_name}] Excepción no controlada en barrio '{nb}': {e}",
                    exc_info=True,
                )
            time.sleep(DELAY_BETWEEN_REQUESTS)

        return all_listings
