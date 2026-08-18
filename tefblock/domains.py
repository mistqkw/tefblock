"""Разбор ссылок/доменов и группы связанных доменов одного сервиса.

Если пользователь блокирует youtube.com, ссылки вида https://youtu.be/xxx
тоже должны блокироваться — для этого используются готовые группы алиасов
для самых частых отвлекающих сервисов.
"""
from __future__ import annotations

from urllib.parse import urlparse

# canonical domain -> все домены того же сервиса, которые нужно блокировать вместе
ALIAS_GROUPS: dict[str, list[str]] = {
    "youtube.com": [
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "youtu.be", "www.youtu.be",
        "youtube-nocookie.com", "www.youtube-nocookie.com",
        "googlevideo.com",
    ],
    "twitter.com": ["twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co"],
    "instagram.com": ["instagram.com", "www.instagram.com", "instagr.am"],
    "tiktok.com": ["tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
    "facebook.com": ["facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "fb.watch"],
    "reddit.com": ["reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"],
    "vk.com": ["vk.com", "www.vk.com", "m.vk.com", "vk.ru", "www.vk.ru"],
    "twitch.tv": ["twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"],
    "discord.com": ["discord.com", "www.discord.com", "discord.gg", "discordapp.com"],
    "telegram.org": ["telegram.org", "web.telegram.org", "t.me"],
    "pinterest.com": ["pinterest.com", "www.pinterest.com", "pin.it"],
    "netflix.com": ["netflix.com", "www.netflix.com"],
    "9gag.com": ["9gag.com", "www.9gag.com"],
}

# обратный индекс: любой домен-алиас -> каноническое имя группы
_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias: canonical for canonical, aliases in ALIAS_GROUPS.items() for alias in aliases
}


def extract_hostname(text: str) -> str:
    """Достаёт hostname из URL или просто возвращает домен, если это не URL."""
    text = text.strip()
    if not text:
        return ""
    if "://" not in text:
        # похоже на голый домен, а не на путь
        candidate = "http://" + text
    else:
        candidate = text
    parsed = urlparse(candidate)
    host = (parsed.netloc or parsed.path).split("/")[0]
    host = host.split(":")[0]  # отбросить порт
    return host.lower().strip().rstrip(".")


def expand_to_group(domain: str) -> list[str]:
    """Для домена возвращает полный список доменов, которые нужно блокировать.

    Если домен входит в известную группу (например youtube.com/youtu.be) —
    возвращаются все домены группы. Иначе — сам домен плюс www.-вариант.
    """
    domain = domain.lower().strip().rstrip(".")
    if not domain:
        return []
    canonical = _ALIAS_TO_CANONICAL.get(domain)
    if canonical:
        return list(ALIAS_GROUPS[canonical])
    variants = {domain}
    if domain.startswith("www."):
        variants.add(domain[4:])
    else:
        variants.add("www." + domain)
    return sorted(variants)


def normalize_site_input(raw: str) -> tuple[str, list[str]]:
    """Принимает ввод пользователя (URL или домен), возвращает (канон.имя, домены-для-блокировки)."""
    host = extract_hostname(raw)
    if not host:
        return "", []
    canonical = _ALIAS_TO_CANONICAL.get(host, host)
    return canonical, expand_to_group(host)
