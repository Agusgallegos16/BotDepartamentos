from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List
from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

from src.config import (
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    NEIGHBORHOOD_SLUGS,
    REQUEST_TIMEOUT,
    Listing,
)
from src.database import Database
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class MercadoLibreScraper(BaseScraper):
    """Scraper for Mercado Libre Inmuebles."""

    portal_name: str = "mercadolibre"
    BASE_URL = "https://inmuebles.mercadolibre.com.ar"

    def __init__(self):
        super().__init__()
        self.db = Database()
        self._thread_local = threading.local()

    def _get_thread_session(self):
        """Devuelve una sesión curl_cffi segura y local al hilo actual."""
        if not hasattr(self._thread_local, "session"):
            sess = requests.Session(impersonate="chrome124")
            sess.headers.update(self.session.headers)
            self._thread_local.session = sess
        return self._thread_local.session

    def _build_url(self, neighborhood_slug: str) -> str:
        max_budget = int(MAX_TOTAL_BUDGET_ARS)
        # En Argentina: 2 ambientes = 1 dormitorio, 3 ambientes = 2 dormitorios.
        # El filtro de URL de Mercado Libre es BEDROOMS (dormitorios).
        min_bedrooms = max(1, MIN_ROOMS - 1)
        clean_slug = neighborhood_slug.replace("-capital-federal", "")
        return (
            f"{self.BASE_URL}/departamentos/alquiler/capital-federal/{clean_slug}/"
            f"_PriceRange_0ARS-{max_budget}_BEDROOMS_{min_bedrooms}-*"
        )

    def _fetch_detail_expenses(self, detail_url: str) -> float:
        """Extrae el valor de expensas accediendo a la ficha de la propiedad."""
        try:
            sess = self._get_thread_session()
            resp = sess.get(detail_url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                return 0.0
            html = resp.text
            parser = LexborHTMLParser(html)

            # 1. Elemento directo de expensas (ej: 'Expensas desde $ 135.000')
            exp_node = parser.css_first(
                "[class*='ui-pdp-maintenance-fee'], [class*='maintenance-fee']"
            )
            if exp_node:
                return self.parse_ars_number(exp_node.text(strip=True))

            # 2. Fila en tabla de especificaciones
            for tr in parser.css("tr.ui-vpp-striped-specs__row, tr.andes-table__row, tr"):
                txt = tr.text(strip=True)
                if "expensa" in txt.lower():
                    return self.parse_ars_number(txt)

            # 3. Regex en texto general
            m = re.search(
                r"expensas[^\d\n\r]{0,25}(?:desde\s*)?\$?\s*([\d\.]+)",
                html,
                re.IGNORECASE,
            )
            if m:
                return self.parse_ars_number(m.group(0))

        except Exception as e:
            logger.debug(f"[{self.portal_name}] Error obteniendo expensas de detalle: {e}")
        return 0.0

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        slug_info = NEIGHBORHOOD_SLUGS.get(neighborhood)
        if not slug_info:
            logger.warning(f"[{self.portal_name}] Barrio no mapeado: '{neighborhood}'")
            return []

        base_url = self._build_url(slug_info["mercadolibre"])
        raw_candidates = []
        seen_portal_ids = set()

        for page in range(1, 4):
            if page == 1:
                page_url = base_url
            else:
                offset = (page - 1) * 48 + 1
                page_url = f"{base_url}_Desde_{offset}_NoIndex_True"

            html_content = self.fetch_html(page_url)
            if not html_content:
                break

            parser = LexborHTMLParser(html_content)
            cards = parser.css("li.ui-search-layout__item")
            if not cards:
                break

            new_on_page = 0
            for card in cards:
                try:
                    # Enlace y Portal ID
                    link_elem = card.css_first("a.poly-component__title, a.ui-search-link, a")
                    if not link_elem:
                        continue

                    full_url = link_elem.attributes.get("href") or ""
                    if not full_url:
                        continue

                    # Limpiar fragmentos y query params extensos de ML
                    if "#" in full_url:
                        full_url = full_url.split("#")[0]
                    if "?" in full_url:
                        full_url = full_url.split("?")[0]

                    # ID extraído de la URL (ej: MLA-2168563171 o MLA123456)
                    id_match = re.search(r"(MLA-?\d+)", full_url, re.IGNORECASE)
                    portal_id = (
                        id_match.group(1).replace("-", "")
                        if id_match
                        else str(hash(full_url))
                    )

                    if portal_id in seen_portal_ids:
                        continue

                    # Precio
                    price_elem = card.css_first(".andes-money-amount__fraction")
                    raw_price = price_elem.text(strip=True) if price_elem else ""
                    price_ars = self.parse_ars_number(raw_price)

                    if price_ars <= 0 or price_ars > MAX_TOTAL_BUDGET_ARS:
                        continue

                    # Título
                    title_elem = card.css_first(
                        ".poly-component__title, .ui-search-item__title, h2"
                    )
                    title = title_elem.text(strip=True) if title_elem else "Departamento"

                    # Ubicación / Dirección
                    loc_elem = card.css_first(
                        ".poly-component__location, .ui-search-item__location"
                    )
                    address = loc_elem.text(strip=True) if loc_elem else slug_info["name"]

                    # Atributos (ambientes, m2)
                    attr_items = card.css(
                        ".poly-attributes-list__item, ul.ui-search-card-attributes li"
                    )
                    attr_texts = [a.text(strip=True) for a in attr_items]
                    combined_text = " ".join([title, address] + attr_texts + [card.text() or ""])

                    rooms = 0
                    m2 = 0.0

                    for attr in attr_texts:
                        attr_lower = attr.lower()
                        if "amb" in attr_lower:
                            rooms = self.extract_rooms(attr)
                        elif "m²" in attr_lower or "m2" in attr_lower:
                            m2_match = re.search(r"(\d+)", attr)
                            if m2_match:
                                m2 = float(m2_match.group(1))

                    if rooms == 0:
                        rooms = self.extract_rooms(combined_text)

                    if 0 < rooms < MIN_ROOMS:
                        continue

                    has_balcony = self.detect_balcony(combined_text)

                    # Imagen
                    img_elem = card.css_first("img")
                    img_url = ""
                    if img_elem:
                        img_url = (
                            img_elem.attributes.get("data-src")
                            or img_elem.attributes.get("src")
                            or ""
                        )

                    # 1. Intentar extraer expensas directamente del texto de la tarjeta
                    expenses_ars = 0.0
                    card_text = card.text() or ""
                    exp_match = re.search(r"expensas\s*[:$]?\s*([\d\.]+)", card_text, re.IGNORECASE)
                    if exp_match:
                        expenses_ars = self.parse_ars_number(exp_match.group(1))

                    # 2. Si no están en la tarjeta, verificar si ya las tenemos en caché de la base de datos
                    listing_db_id = f"{self.portal_name}_{portal_id}"
                    if expenses_ars <= 0.0:
                        cached_exp = self.db.get_cached_expenses(listing_db_id, price_ars)
                        if cached_exp is not None:
                            expenses_ars = cached_exp

                    raw_candidates.append({
                        "portal_id": portal_id,
                        "full_url": full_url,
                        "title": title,
                        "address": address,
                        "price_ars": price_ars,
                        "expenses_ars": expenses_ars,
                        "rooms": rooms,
                        "m2": m2,
                        "has_balcony": has_balcony,
                        "img_url": img_url,
                    })
                    seen_portal_ids.add(portal_id)
                    new_on_page += 1

                except Exception as e:
                    logger.debug(f"[{self.portal_name}] Error en tarjeta ML: {e}")
                    continue

            # Si no hubo avisos nuevos o la página tuvo menos de 48 tarjetas, cortar
            if new_on_page == 0 or len(cards) < 48:
                break

        # 3. Resolver expensas faltantes en paralelo solo para candidatos que las necesitan
        pending_expenses = [
            c for c in raw_candidates if c["expenses_ars"] <= 0.0 and c["price_ars"] < MAX_TOTAL_BUDGET_ARS
        ]

        if pending_expenses:
            def _resolve_exp(cand):
                cand["expenses_ars"] = self._fetch_detail_expenses(cand["full_url"])

            with ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(_resolve_exp, pending_expenses))

        listings: List[Listing] = []
        for c in raw_candidates:
            total_cost = c["price_ars"] + c["expenses_ars"]
            if total_cost > MAX_TOTAL_BUDGET_ARS:
                continue

            # Descartar si el alquiler está a menos de $150.000 del tope sin expensas especificadas
            if c["expenses_ars"] <= 0.0 and c["price_ars"] >= (MAX_TOTAL_BUDGET_ARS - 150000):
                continue

            listing = Listing(
                id=f"{self.portal_name}_{c['portal_id']}",
                portal=self.portal_name,
                portal_id=c["portal_id"],
                url=c["full_url"],
                title=c["title"],
                neighborhood=slug_info["name"],
                address=c["address"],
                price_ars=c["price_ars"],
                expenses_ars=c["expenses_ars"],
                total_cost_ars=total_cost,
                rooms=c["rooms"],
                m2=c["m2"],
                has_balcony=c["has_balcony"],
                thumbnail_url=c["img_url"],
            )
            listings.append(listing)

        return listings
