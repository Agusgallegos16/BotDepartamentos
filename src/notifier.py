from __future__ import annotations

import html
import logging
import sys
from typing import Optional
import requests

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, Listing

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Dispatches formatted property alerts to Telegram using HTML parse mode."""

    def __init__(
        self,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.enabled = bool(self.bot_token and self.chat_id and self.bot_token != "TU_TELEGRAM_BOT_TOKEN_AQUI")

        if not self.enabled:
            logger.warning(
                "Telegram credentials not configured. Notifications will be printed to console."
            )

    @staticmethod
    def _format_currency(amount: float) -> str:
        """Format number into Argentinian Peso readable format ($1.050.000)."""
        return f"${amount:,.0f}".replace(",", ".")

    def build_message_html(
        self,
        listing: Listing,
        event_type: str = "NEW",
        old_price: Optional[float] = None,
    ) -> str:
        """Constructs safe HTML formatted text for Telegram."""
        safe_title = html.escape(listing.title or "Departamento en alquiler")
        safe_neighborhood = html.escape(listing.neighborhood or "CABA")
        safe_address = html.escape(listing.address or "")
        PORTAL_DISPLAY_NAMES = {
            "mob": "Mob.ar",
            "cabaprop": "Cabaprop",
            "directoaldueno": "Directo al Dueño",
            "argenprop": "Argenprop",
            "zonaprop": "Zonaprop",
            "mercadolibre": "Mercado Libre",
            "remax": "RE/MAX",
        }
        portal_display = PORTAL_DISPLAY_NAMES.get(listing.portal.lower(), listing.portal.capitalize())
        safe_portal = html.escape(portal_display)

        # Badge de evento
        if event_type == "PRICE_DROP" and old_price:
            diff = old_price - listing.total_cost_ars
            header = (
                f"📉 <b>REBAJA DE PRECIO: -{self._format_currency(diff)}</b>"
            )
        else:
            header = "🚨 <b>NUEVA PROPIEDAD DETECTADA</b>"

        # Formato de precios
        total_str = self._format_currency(listing.total_cost_ars)
        rent_str = self._format_currency(listing.price_ars)
        exp_str = (
            self._format_currency(listing.expenses_ars)
            if listing.expenses_ars > 0
            else "No especificadas"
        )

        # Detalles
        rooms_text = f"{listing.rooms} amb." if listing.rooms > 0 else "Ambientes a consultar"
        m2_text = f"{int(listing.m2)} m²" if listing.m2 > 0 else ""
        balcony_badge = " · ☀️ <b>Con balcón</b>" if listing.has_balcony else ""

        specs = f"📐 {rooms_text}"
        if m2_text:
            specs += f" · {m2_text}"
        specs += balcony_badge

        address_line = f"📍 <b>{safe_neighborhood}</b>"
        if safe_address:
            address_line += f" ({safe_address})"

        msg = (
            f"{header}\n\n"
            f"💰 <b>Total: {total_str}</b>\n"
            f"💵 Alquiler: {rent_str} | Expensas: {exp_str}\n\n"
            f"{address_line}\n"
            f"{specs}\n\n"
            f"📝 <i>{safe_title[:100]}...</i>\n\n"
            f"🔗 <a href=\"{listing.url}\">Ver publicación en {safe_portal}</a>"
        )
        return msg

    def notify(
        self,
        listing: Listing,
        event_type: str = "NEW",
        old_price: Optional[float] = None,
    ) -> bool:
        """Sends notification via Telegram API or prints to console if disabled."""
        message_html = self.build_message_html(listing, event_type, old_price)

        if not self.enabled:
            clean_text = (
                message_html.replace("<b>", "")
                .replace("</b>", "")
                .replace("<i>", "")
                .replace("</i>", "")
            )
            try:
                print("\n" + "=" * 60)
                print("[CONSOLA - NOTIFICACIÓN MOCK]")
                print(clean_text)
                if listing.thumbnail_url:
                    print(f"Foto URL: {listing.thumbnail_url}")
                print("=" * 60 + "\n")
            except UnicodeEncodeError:
                # Fallback for Windows consoles using non-utf8 codepages (e.g. cp1252)
                encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
                safe_text = clean_text.encode(encoding, errors="replace").decode(encoding)
                print("\n" + "=" * 60)
                print("[CONSOLA - NOTIFICACION MOCK (SAFE ENCODING)]")
                print(safe_text)
                if listing.thumbnail_url:
                    print(f"Foto URL: {listing.thumbnail_url}")
                print("=" * 60 + "\n")
            return True

        # Try sending with photo if available
        if listing.thumbnail_url and listing.thumbnail_url.startswith("http"):
            try:
                photo_url = f"{self.base_url}/sendPhoto"
                payload = {
                    "chat_id": self.chat_id,
                    "photo": listing.thumbnail_url,
                    "caption": message_html,
                    "parse_mode": "HTML",
                }
                res = requests.post(photo_url, json=payload, timeout=15)
                if res.status_code == 200:
                    return True
                logger.warning(
                    f"Telegram sendPhoto failed ({res.status_code}): {res.text}. Falling back to sendMessage."
                )
            except Exception as e:
                logger.warning(f"Error sending photo to Telegram: {e}. Falling back to sendMessage.")

        # Fallback to text message
        try:
            msg_url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message_html,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            res = requests.post(msg_url, json=payload, timeout=15)
            if res.status_code == 200:
                return True
            logger.error(f"Telegram sendMessage failed ({res.status_code}): {res.text}")
            return False
        except Exception as e:
            logger.error(f"Error calling Telegram sendMessage API: {e}")
            return False
