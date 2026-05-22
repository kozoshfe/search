# OLX Position Checker

Невелика програмка, яка спочатку бере твої актуальні оголошення зі сторінки продавця OLX, потім перевіряє сторінку пошуку і показує, скільки твоїх оголошень є на першій сторінці та на яких позиціях вони стоять.

## Як користуватись

1. Перевір, що в `seller-url.txt` є твоя сторінка продавця.
2. Перевір, що в `search-url.txt` є потрібний пошук OLX.
3. Запусти:

```bash
python3 olx_position_checker.py
```

Скрипт сам збере ID твоїх оголошень зі сторінки продавця і збереже їх у `my-ads-from-seller.txt`.

Разова перевірка без редагування `my-ads.txt`:

```bash
python3 olx_position_checker.py --ad "HP ZBook Firefly 15 G8"
```

Ручний режим через `my-ads.txt`, без сторінки продавця:

```bash
python3 olx_position_checker.py --manual
```

## Інший пошук

Поточне посилання збережене в `search-url.txt`. Можеш замінити його на будь-який інший пошук OLX.

Також можна передати URL одразу в команді:

```bash
python3 olx_position_checker.py --url "https://www.olx.ua/uk/..."
```

Іншу сторінку продавця можна передати так:

```bash
python3 olx_position_checker.py --seller-url "https://noutok.olx.ua/uk/home/"
```

## Автоматичний запуск у GitHub

У репозиторії є workflow `.github/workflows/hourly-check.yml`.

Він запускається:

- автоматично раз на годину, приблизно на 17-й хвилині;
- вручну з вкладки `Actions` через кнопку `Run workflow`.

Результат буде видно у `Actions` всередині конкретного запуску. Повні файли звіту зберігаються як artifact `olx-report`.

Важливо: GitHub Actions `schedule` працює в режимі best-effort. GitHub може запускати workflow із затримкою або інколи пропускати scheduled run, особливо на високому навантаженні.

## Telegram канал

Щоб звіт відправлявся в Telegram канал через GitHub Actions, додай ці secrets у `Settings` -> `Secrets and variables` -> `Actions`:

```text
TELEGRAM_BOT_TOKEN = token від BotFather
TELEGRAM_CHAT_ID = @username_каналу
```

Як отримати значення:

1. Створи бота через `@BotFather` і скопіюй token.
2. Додай бота адміністратором у свій Telegram канал.

Якщо канал приватний і без username, треба використати його numeric chat id. Для каналів він зазвичай виглядає так:

```text
-1001234567890
```

Тоді secret має бути:

```text
TELEGRAM_CHAT_ID = -1001234567890
```

## Важливо

Скрипт перевіряє саме ту сторінку, яку відкриває OLX за посиланням. Якщо твоє оголошення на другій сторінці пошуку, треба передати URL другої сторінки або замінити посилання в `search-url.txt`.
