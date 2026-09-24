"""Un publisher per canale. `build_publishers()` crea solo quelli configurati/abilitati."""
from promo.publishers.base import Publisher, PublishResult, post_text

__all__ = ["Publisher", "PublishResult", "post_text", "build_publishers"]


def build_publishers(settings) -> dict:
    from promo.publishers.telegram import TelegramChannelPublisher
    from promo.publishers.tiktok import TikTokDraftPublisher, token_store
    from promo.publishers.x import XPublisher

    publishers = {
        "telegram_channel": TelegramChannelPublisher(settings.bot_token, settings.telegram_channel_id),
        "tiktok": TikTokDraftPublisher(settings.tiktok_client_key, settings.tiktok_client_secret,
                                       token_store(settings)),
    }
    if settings.x_enabled:
        publishers["x"] = XPublisher()
    return publishers
