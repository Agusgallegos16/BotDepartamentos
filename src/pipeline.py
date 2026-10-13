from __future__ import annotations

import logging
import time
from typing import Dict, List, Type

from src.config import ACTIVE_PORTALS, Listing
from src.database import Database
from src.notifier import TelegramNotifier
from src.scrapers.base import BaseScraper
from src.scrapers.argenprop import ArgenpropScraper
from src.scrapers.zonaprop import ZonapropScraper
from src.scrapers.mercadolibre import MercadoLibreScraper
from src.scrapers.remax import RemaxScraper

logger = logging.getLogger(__name__)

SCRAPER_REGISTRY: Dict[str, Type[BaseScraper]] = {
    "argenprop": ArgenpropScraper,
    "zonaprop": ZonapropScraper,
    "mercadolibre": MercadoLibreScraper,
    "remax": RemaxScraper,
}


class Pipeline:
    """Orchestrates scraping, business evaluation, persistence, and dispatch."""

    def __init__(self):
        self.db = Database()
        self.notifier = TelegramNotifier()

    def run(self) -> Dict[str, int]:
        """
        Executes a single scan cycle across all configured portals.
        Returns execution statistics.
        """
        start_time = time.time()
        logger.info("==================================================")
        logger.info("INICIANDO CICLO DE BÚSQUEDA DE DEPARTAMENTOS")
        logger.info("==================================================")

        stats = {
            "total_extracted": 0,
            "new_notified": 0,
            "price_drops": 0,
            "existing": 0,
            "errors": 0,
        }

        for portal_key in ACTIVE_PORTALS:
            scraper_cls = SCRAPER_REGISTRY.get(portal_key)
            if not scraper_cls:
                logger.warning(f"Portal '{portal_key}' no está registrado en el sistema.")
                continue

            try:
                logger.info(f">>> Ejecutando scraper para: {portal_key.upper()}")
                scraper = scraper_cls()
                listings: List[Listing] = scraper.scrape_all()
                stats["total_extracted"] += len(listings)

                for item in listings:
                    try:
                        status, old_price = self.db.process_listing(item)

                        if status == "NEW":
                            stats["new_notified"] += 1
                            logger.info(
                                f"[NUEVO] {item.portal.upper()} - {item.title[:45]} | ${item.total_cost_ars:,.0f}"
                            )
                            self.notifier.notify(item, event_type="NEW")

                        elif status == "PRICE_DROP":
                            stats["price_drops"] += 1
                            logger.info(
                                f"[REBAJA] {item.portal.upper()} - De ${old_price:,.0f} a ${item.total_cost_ars:,.0f}"
                            )
                            self.notifier.notify(
                                item, event_type="PRICE_DROP", old_price=old_price
                            )

                        else:
                            stats["existing"] += 1
                    except Exception as item_err:
                        logger.error(f"Error procesando aviso individual ({item.id}): {item_err}")

            except Exception as e:
                stats["errors"] += 1
                # DIRECTIVA 2: Fail-soft absoluto para que el pipeline no se detenga
                logger.error(
                    f"Error crítico en el scraper de {portal_key.upper()}: {e}",
                    exc_info=True,
                )

        elapsed = time.time() - start_time
        total_in_db = self.db.get_total_listings()

        logger.info("==================================================")
        logger.info("RESUMEN DEL CICLO")
        logger.info(f"Tiempo de ejecución: {elapsed:.2f} segundos")
        logger.info(f"Total avisos procesados: {stats['total_extracted']}")
        logger.info(f"Nuevos avisos alertados: {stats['new_notified']}")
        logger.info(f"Rebajas de precio alertadas: {stats['price_drops']}")
        logger.info(f"Avisos sin cambios: {stats['existing']}")
        logger.info(f"Errores en scrapers: {stats['errors']}")
        logger.info(f"Total acumulado en base de datos: {total_in_db}")
        logger.info("==================================================")

        return stats
