#!/usr/bin/env python3
import datetime as dt
import html as html_lib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SEARCH_FILE = Path("search-url.txt")
SELLER_FILE = Path("seller-url.txt")
MANUAL_MARKERS_FILE = Path("my-ads.txt")
SELLER_CACHE_FILE = Path("my-ads-from-seller.txt")
REPORT_FILE = Path("olx-report.txt")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    ),
}


def main():
    args = parse_args(sys.argv[1:])
    search_url = args["search_url"] or read_first_url(SEARCH_FILE)
    markers = get_markers(args)

    if not markers:
        print("Не знайшов твоїх оголошень.")
        print(f"Додай сторінку продавця в {SELLER_FILE} або впиши оголошення в {MANUAL_MARKERS_FILE}.")
        return 1

    print("Перевіряю сторінку пошуку OLX...")

    search_html = fetch_html(search_url)
    listings = parse_listings(search_html, search_url)
    matches = find_matches(listings, markers)
    message = print_report(listings, matches, markers, search_url)
    send_telegram_if_configured(message)
    return 0


def parse_args(argv):
    parsed = {"search_url": "", "seller_url": "", "markers": [], "manual": False}
    index = 0

    while index < len(argv):
        arg = argv[index]
        nxt = argv[index + 1] if index + 1 < len(argv) else ""

        if arg in ("--url", "-u") and nxt:
            parsed["search_url"] = nxt
            index += 2
            continue

        if arg in ("--seller-url", "--seller", "-s") and nxt:
            parsed["seller_url"] = nxt
            index += 2
            continue

        if arg == "--manual":
            parsed["manual"] = True
            index += 1
            continue

        if arg in ("--ad", "--marker", "-m") and nxt:
            parsed["markers"].append(nxt)
            index += 2
            continue

        if not parsed["seller_url"] and is_seller_home_url(arg):
            parsed["seller_url"] = arg
        elif not parsed["search_url"] and looks_like_url(arg):
            parsed["search_url"] = arg
        else:
            parsed["markers"].append(arg)

        index += 1

    return parsed


def get_markers(args):
    if args["markers"]:
        return [to_marker(value) for value in args["markers"]]

    seller_url = args["seller_url"] or read_first_url(SELLER_FILE, required=False)
    if seller_url and not args["manual"]:
        print("Збираю твої актуальні оголошення зі сторінки продавця...")

        seller_html = fetch_html(seller_url)
        seller_listings = parse_listings(seller_html, seller_url)
        save_seller_cache(seller_url, seller_listings)

        print(f"Знайдено твоїх актуальних оголошень: {len(seller_listings)}")

        return [listing_to_marker(listing) for listing in seller_listings]

    return read_markers(MANUAL_MARKERS_FILE)


def read_first_url(path, required=True):
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except FileNotFoundError:
        if not required:
            return ""
        raise

    if required:
        raise RuntimeError(f"Не знайшов URL у {path}")
    return ""


def read_markers(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    return [
        to_marker(line.strip())
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def fetch_html(url):
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_listings(page_html, base_url):
    cards = extract_card_html(page_html)
    listings = [listing for listing in (parse_card(card, base_url) for card in cards) if listing]
    fallback = listings or parse_links_fallback(page_html, base_url)

    unique = []
    seen = set()
    for listing in fallback:
        key = listing.get("id") or listing.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        listing["position"] = len(unique) + 1
        unique.append(listing)
    return unique


def extract_card_html(page_html):
    starts = []
    for match in re.finditer(r"data-cy=[\"']l-card[\"']", page_html, re.I):
        div_start = page_html.rfind("<div", 0, match.start())
        starts.append(div_start if div_start >= 0 else match.start())

    return [
        page_html[start : starts[index + 1] if index + 1 < len(starts) else len(page_html)]
        for index, start in enumerate(starts)
    ]


def parse_card(card_html, base_url):
    link = find_ad_link(card_html, base_url)
    if not link:
        return None

    title = (
        extract_attribute(card_html, "alt")
        or extract_attribute(card_html, "title")
        or extract_by_regex(card_html, r"data-cy=[\"']ad-card-title[\"'][^>]*>([\s\S]*?)</[^>]+>")
        or extract_by_regex(card_html, r"<h[1-6][^>]*>([\s\S]*?)</h[1-6]>")
        or ""
    )

    return {"title": clean_text(title), "url": link, "id": extract_olx_id(link)}


def parse_links_fallback(page_html, base_url):
    listings = []
    link_re = re.compile(r"<a\b[^>]*href=([\"'])(.*?)\1[^>]*>([\s\S]*?)</a>", re.I)
    for match in link_re.finditer(page_html):
        url = urljoin(base_url, decode_html(match.group(2)))
        if not is_ad_url(url):
            continue
        listings.append({"title": clean_text(match.group(3)), "url": url, "id": extract_olx_id(url)})
    return listings


def find_ad_link(fragment, base_url):
    for match in re.finditer(r"<a\b[^>]*href=([\"'])(.*?)\1", fragment, re.I):
        url = urljoin(base_url, decode_html(match.group(2)))
        if is_ad_url(url):
            return url
    return ""


def is_ad_url(url):
    return bool(re.search(r"olx\.ua/d/", url, re.I) and re.search(r"\.html(?:[?#].*)?$", url, re.I))


def find_matches(listings, markers):
    matches = []
    for listing in listings:
        listing_url = normalize_url(listing.get("url", ""))
        listing_title = normalize_text(listing.get("title", ""))
        matched_by = []

        for marker in markers:
            same_id = marker["id"] and listing.get("id") and marker["id"].lower() == listing["id"].lower()
            same_url = (
                marker["normalized_url"]
                and (
                    listing_url == marker["normalized_url"]
                    or listing_url in marker["normalized_url"]
                    or marker["normalized_url"] in listing_url
                )
            )
            same_title = (
                marker.get("match_title", True)
                and len(marker["normalized_text"]) >= 3
                and marker["normalized_text"] in listing_title
            )

            if same_id or same_url or same_title:
                matched_by.append(marker["raw"])

        if matched_by:
            item = dict(listing)
            item["matched_by"] = matched_by
            matches.append(item)
    return matches


def print_report(listings, matches, markers, search_url):
    unmatched = [
        marker
        for marker in markers
        if not any(marker["raw"] in match["matched_by"] for match in matches)
    ]

    write_text_report(listings, matches, unmatched, search_url)

    summary_lines = [
        "OLX Position Checker",
        "=" * 56,
        f"Твоїх ID:    {len(markers)}",
        f"На сторінці: {len(listings)} оголошення",
        f"Знайдено:    {len(matches)}",
        "=" * 56,
    ]
    print("\n".join(summary_lines))

    if matches:
        print()
        print("ЗНАЙДЕНІ ОГОЛОШЕННЯ")
        print("-" * 56)
        for match in matches:
            print(f"{('#' + str(match['position'])):<5} {match.get('id', '-'):<10} {shorten(match.get('title') or 'Без назви', 48)}")
    else:
        print()
        print("Твої оголошення на цій сторінці не знайдені.")

    print()
    print(f"Повний звіт з посиланнями збережено у файлі: {REPORT_FILE}")

    return build_telegram_message(listings, matches, markers)


def write_text_report(listings, matches, unmatched, search_url):
    lines = [
        "OLX Position Checker",
        f"Дата: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Пошук: {search_url}",
        "",
        f"Усього оголошень на сторінці: {len(listings)}",
        f"Твоїх оголошень знайдено: {len(matches)}",
        f"Не знайдено: {len(unmatched)}",
        "",
        "ЗНАЙДЕНІ ОГОЛОШЕННЯ",
        "-" * 80,
    ]

    if matches:
        for match in matches:
            lines.extend(
                [
                    f"Позиція: #{match['position']}",
                    f"ID: {match.get('id', '-')}",
                    f"Назва: {match.get('title') or 'Без назви'}",
                    f"URL: {match['url']}",
                    "",
                ]
            )
    else:
        lines.append("На цій сторінці пошуку твої оголошення не знайдені.")
        lines.append("")

    if unmatched:
        lines.extend(["НЕ ЗНАЙДЕНІ ID", "-" * 80])
        lines.extend(marker["raw"] for marker in unmatched)
        lines.append("")

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


def build_telegram_message(listings, matches, markers):
    lines = [
        f"OLX: знайдено {len(matches)} з {len(markers)}",
    ]

    if matches:
        lines.append("")
        for match in matches:
            title = shorten(clean_telegram_title(match.get("title") or "Без назви"), 58)
            lines.append(f"#{match['position']}  {match.get('id', '-')}  {title}")
    else:
        lines.append("")
        lines.append("На цій сторінці твої оголошення не знайдені.")

    return "\n".join(lines)


def clean_telegram_title(value):
    value = re.sub(r"\.css-[a-z0-9-]+\{[^}]*\}", " ", value or "", flags=re.I)
    value = re.sub(r"[-a-z]+:[^;\s]+;?", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def send_telegram_if_configured(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        if os.environ.get("GITHUB_ACTIONS"):
            missing = []
            if not token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not chat_id:
                missing.append("TELEGRAM_CHAT_ID")
            print(f"Telegram: не налаштовано, немає secret: {', '.join(missing)}")
        return

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": message[:3900],
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            if response.status >= 400:
                raise RuntimeError(f"Telegram повернув HTTP {response.status}")
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram повернув HTTP {error.code}: {details}") from error

    print("Telegram: звіт відправлено.")


def shorten(value, max_length):
    value = re.sub(r"\s+", " ", value or "").strip()
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def shorten_url(value, max_length):
    return shorten(value.replace("https://", ""), max_length)


def save_seller_cache(seller_url, listings):
    lines = [
        "# Автоматично зібрано зі сторінки продавця.",
        f"# Джерело: {seller_url}",
        f"# Оновлено: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        "# Формат: ID | Назва | URL",
        "",
    ]
    lines.extend(
        f"{listing.get('id') or '-'} | {listing.get('title') or 'Без назви'} | {listing.get('url') or ''}"
        for listing in listings
    )
    lines.append("")
    SELLER_CACHE_FILE.write_text("\n".join(lines), encoding="utf-8")


def to_marker(raw):
    return {
        "raw": raw,
        "normalized_text": normalize_text(raw),
        "normalized_url": normalize_url(raw) if looks_like_url(raw) else "",
        "id": extract_olx_id(raw),
        "match_title": True,
    }


def listing_to_marker(listing):
    return {
        "raw": listing.get("id") or listing.get("url") or listing.get("title", ""),
        "title": listing.get("title", ""),
        "url": listing.get("url", ""),
        "normalized_text": normalize_text(listing.get("title", "")),
        "normalized_url": normalize_url(listing.get("url", "")),
        "id": listing.get("id", ""),
        "match_title": False,
    }


def extract_by_regex(value, pattern):
    match = re.search(pattern, value, re.I)
    return match.group(1) if match else ""


def extract_attribute(value, name):
    match = re.search(rf"{re.escape(name)}=([\"'])(.*?)\1", value, re.I)
    return match.group(2) if match else ""


def looks_like_url(value):
    return bool(re.match(r"^https?://", value or "", re.I))


def is_seller_home_url(value):
    if not looks_like_url(value):
        return False
    parsed = urlparse(value)
    return parsed.hostname and parsed.hostname.endswith(".olx.ua") and re.search(r"/(?:uk/)?home/?$", parsed.path, re.I)


def normalize_url(value):
    try:
        parsed = urlparse(value.strip())
        filtered_query = [
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if not re.match(r"^(utm_|search|reason|ref)", key, re.I)
        ]
        cleaned = parsed._replace(query=urlencode(filtered_query), fragment="")
        return urlunparse(cleaned).rstrip("/").lower()
    except Exception:
        return value.strip().rstrip("/").lower()


def extract_olx_id(value):
    match = re.search(r"\bID([a-z0-9]+)(?:\.html)?\b", value or "", re.I)
    return f"ID{match.group(1)}" if match else ""


def normalize_text(value):
    value = decode_html(value or "").lower()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.U)
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value):
    value = decode_html(value or "")
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def decode_html(value):
    return html_lib.unescape(value or "")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Помилка: {error}", file=sys.stderr)
        raise SystemExit(1)
