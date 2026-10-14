from __future__ import annotations

import logging
import os
import re
import time
from typing import List
from selectolax.lexbor import LexborHTMLParser

from src.config import (
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    NEIGHBORHOOD_SLUGS,
    TARGET_NEIGHBORHOODS,
    Listing,
)
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ArgenpropScraper(BaseScraper):
    """Scraper for Argenprop with robust fail-soft against AWS WAF challenges."""

    portal_name: str = "argenprop"
    BASE_URL = "https://www.argenprop.com"

    def __init__(self):
        super().__init__()
        # Permite inyectar cookie o aws-waf-token si se configuró en el .env
        custom_token = os.getenv("ARGENPROP_WAF_TOKEN", "").strip()
        custom_cookie = os.getenv("ARGENPROP_COOKIE", "").strip()
        if custom_token:
            self.session.cookies.set("aws-waf-token", custom_token, domain=".argenprop.com")
        elif custom_cookie:
            for item in custom_cookie.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    self.session.cookies.set(k.strip(), v.strip(), domain=".argenprop.com")

    def _match_neighborhood(self, text: str) -> str:
        """Determina a qué barrio objetivo pertenece el aviso a partir del texto (título y dirección)."""
        text_lower = text.lower()
        aliases = {
            "palermo": [
                "palermo", "botánico", "botanico", "las cañitas", "cañitas",
                "palermo soho", "palermo hollywood", "palermo chico", "palermo viejo"
            ],
            "caballito": [
                "caballito", "parque centenario", "primera junta", "caballito norte", "caballito sur"
            ],
            "belgrano": [
                "belgrano", "barrancas de belgrano", "belgrano r", "belgrano c", "belgrano chico"
            ],
            "villa-urquiza": ["villa urquiza", "urquiza r"],
            "villa-del-parque": ["villa del parque"],
            "chacarita": ["chacarita"],
            "recoleta": ["recoleta", "barrio norte"],
            "colegiales": ["colegiales"],
            "villa-crespo": ["villa crespo"],
        }
        for nb_key in TARGET_NEIGHBORHOODS:
            nb_aliases = aliases.get(nb_key, [nb_key.replace("-", " ")])
            for alias in nb_aliases:
                if re.search(r"\b" + re.escape(alias) + r"\b", text_lower):
                    return NEIGHBORHOOD_SLUGS.get(nb_key, {}).get("name", nb_key)
        return ""

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        """Implementado para compatibilidad con la interfaz BaseScraper. Reutiliza scrape_all."""
        all_results = self.scrape_all()
        target_name = NEIGHBORHOOD_SLUGS.get(neighborhood, {}).get("name", "").lower()
        return [l for l in all_results if l.neighborhood.lower() == target_name]

    def scrape_all(self) -> List[Listing]:
        """
        Extrae propiedades recientes de Capital Federal en un máximo de 3 páginas,
        evitando el bloqueo por ráfagas de AWS WAF y cubriendo todos los barrios objetivo configurados.
        """
        all_listings: List[Listing] = []
        seen_ids = set()
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        if MIN_ROOMS <= 1:
            room_slug = "1-ambiente-o-2-ambientes-o-3-ambientes"
        elif MIN_ROOMS == 2:
            room_slug = "2-ambientes-o-3-ambientes"
        elif MIN_ROOMS == 3:
            room_slug = "3-ambientes-o-4-ambientes"
        else:
            room_slug = f"{MIN_ROOMS}-ambientes"

        base_url = f"{self.BASE_URL}/departamentos/alquiler/capital-federal/{room_slug}?orden=MasNuevos"

        for page in range(1, 4):
            if getattr(self, "is_blocked", False):
                break

            if page > 1:
                time.sleep(2.0)

            page_url = (
                base_url
                if page == 1
                else f"{self.BASE_URL}/departamentos/alquiler/capital-federal/{room_slug}?pagina-{page}&orden=MasNuevos"
            )
            extra_headers = {}
            if page > 1:
                extra_headers = {
                    "Referer": base_url,
                    "Sec-Fetch-Site": "same-origin",
                }

            html_content = self.fetch_html(page_url, headers=extra_headers)
            if not html_content:
                break

            parser = LexborHTMLParser(html_content)
            cards = parser.css("div.listing__item")
            if not cards:
                logger.debug(
                    f"[{self.portal_name}] No se encontraron tarjetas en {page_url}."
                )
                break

            new_on_page = 0
            for card in cards:
                try:
                    link_elem = card.css_first("a.card, a")
                    if not link_elem:
                        continue

                    href = link_elem.attributes.get("href") or ""
                    if not href:
                        continue

                    full_url = f"{self.BASE_URL}{href}" if href.startswith("/") else href

                    id_match = re.search(r"--(\d+)", href)
                    portal_id = (
                        id_match.group(1)
                        if id_match
                        else card.attributes.get("data-item-id", str(hash(href)))
                    )

                    if portal_id in seen_ids:
                        continue

                    # Título y dirección
                    title_elem = card.css_first("h2.card__title, p.card__title--primary")
                    title = title_elem.text(strip=True) if title_elem else "Departamento"

                    addr_elem = card.css_first("p.card__address, .card__address")
                    address = addr_elem.text(strip=True) if addr_elem else ""

                    # Validar si pertenece a los barrios objetivo
                    matched_nb = self._match_neighborhood(f"{title} {address}")
                    if not matched_nb:
                        continue

                    # Precio
                    price_elem = card.css_first("p.card__price")
                    raw_price = price_elem.text(strip=True) if price_elem else ""
                    price_ars = self.parse_ars_number(raw_price)

                    # Descartar nulos, en USD o fuera de rango
                    if price_ars < 150000 or price_ars > MAX_TOTAL_BUDGET_ARS:
                        continue

                    # Expensas
                    exp_elem = card.css_first("span.card__expenses, .card__expenses")
                    raw_exp = exp_elem.text(strip=True) if exp_elem else ""
                    expenses_ars = self.parse_ars_number(raw_exp)

                    total_cost = price_ars + (expenses_ars or 0.0)
                    if total_cost > MAX_TOTAL_BUDGET_ARS:
                        continue

                    # Descartar si el alquiler está a menos de $150.000 del tope sin expensas especificadas
                    if expenses_ars <= 0.0 and price_ars >= (MAX_TOTAL_BUDGET_ARS - 150000):
                        continue

                    # Características (ambientes, m2)
                    feature_items = card.css("ul.card__main-features li")
                    feature_texts = [f.text(strip=True) for f in feature_items]
                    all_text = " ".join([title, address] + feature_texts)

                    rooms = 0
                    m2 = 0.0

                    for feat in feature_texts:
                        feat_lower = feat.lower()
                        if "amb" in feat_lower:
                            rooms = self.extract_rooms(feat)
                        elif "m²" in feat_lower or "m2" in feat_lower:
                            m2_match = re.search(r"(\d+)", feat)
                            if m2_match:
                                m2 = float(m2_match.group(1))

                    if rooms == 0:
                        rooms = self.extract_rooms(all_text)

                    if 0 < rooms < MIN_ROOMS:
                        continue

                    has_balcony = self.detect_balcony(all_text)

                    # Imagen
                    img_elem = card.css_first("img")
                    img_url = ""
                    if img_elem:
                        img_url = (
                            img_elem.attributes.get("data-src")
                            or img_elem.attributes.get("src")
                            or ""
                        )

                    listing = Listing(
                        id=f"{self.portal_name}_{portal_id}",
                        portal=self.portal_name,
                        portal_id=portal_id,
                        url=full_url,
                        title=title,
                        neighborhood=matched_nb,
                        address=address or matched_nb,
                        price_ars=price_ars,
                        expenses_ars=expenses_ars,
                        rooms=rooms,
                        m2=m2,
                        has_balcony=has_balcony,
                        thumbnail_url=img_url,
                    )
                    seen_ids.add(portal_id)
                    all_listings.append(listing)
                    new_on_page += 1

                except Exception as e:
                    logger.debug(f"[{self.portal_name}] Error en tarjeta: {e}")
                    continue

            if new_on_page == 0 or len(cards) < 20:
                break

        logger.info(
            f"[{self.portal_name}] Total extraídos en barrios objetivo: {len(all_listings)}"
        )
        return all_listings
