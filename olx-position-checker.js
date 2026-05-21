import fs from "node:fs/promises";

const DEFAULT_SEARCH_FILE = "search-url.txt";
const DEFAULT_MARKERS_FILE = "my-ads.txt";
const DEFAULT_SELLER_FILE = "seller-url.txt";
const SELLER_CACHE_FILE = "my-ads-from-seller.txt";
const cli = parseArgs(process.argv.slice(2));

const REQUEST_HEADERS = {
  "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "accept-language": "uk-UA,uk;q=0.9,en;q=0.7",
  "user-agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
};

async function main() {
  const searchUrl = await getSearchUrl();
  const markers = await getMarkers();

  if (!markers.length) {
    console.log("Не знайшов твоїх оголошень.");
    console.log(`Додай сторінку продавця в ${DEFAULT_SELLER_FILE} або впиши оголошення в ${DEFAULT_MARKERS_FILE}.`);
    process.exitCode = 1;
    return;
  }

  console.log("Перевіряю сторінку OLX...");
  console.log(searchUrl);
  console.log("");

  const html = await fetchHtml(searchUrl);
  const listings = parseListings(html, searchUrl);
  const matches = findMatches(listings, markers);

  printReport({ listings, matches, markers });
}

async function getSearchUrl() {
  if (cli.searchUrl) return cli.searchUrl;

  const file = await fs.readFile(DEFAULT_SEARCH_FILE, "utf8");
  const url = file
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith("#"));

  if (!url) {
    throw new Error(`Не знайшов URL у ${DEFAULT_SEARCH_FILE}`);
  }

  return url;
}

async function readMarkers(filePath) {
  const file = await fs.readFile(filePath, "utf8");
  return file
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map(toMarker);
}

async function getMarkers() {
  if (cli.markers.length) {
    return cli.markers.map(toMarker);
  }

  const sellerUrl = await getSellerUrl();
  if (sellerUrl && !cli.manual) {
    console.log("Спочатку збираю твої актуальні оголошення зі сторінки продавця...");
    console.log(sellerUrl);
    console.log("");

    const sellerHtml = await fetchHtml(sellerUrl);
    const sellerListings = parseListings(sellerHtml, sellerUrl);
    await saveSellerCache(sellerUrl, sellerListings);

    console.log(`Знайшов на сторінці продавця оголошень: ${sellerListings.length}`);
    console.log(`Зберіг список у ${SELLER_CACHE_FILE}`);
    console.log("");

    return sellerListings.map(listingToMarker);
  }

  return readMarkers(DEFAULT_MARKERS_FILE);
}

function parseArgs(args) {
  const parsed = { searchUrl: "", sellerUrl: "", markers: [], manual: false };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    const next = args[index + 1];

    if ((arg === "--url" || arg === "-u") && next) {
      parsed.searchUrl = next;
      index += 1;
      continue;
    }

    if ((arg === "--seller-url" || arg === "--seller" || arg === "-s") && next) {
      parsed.sellerUrl = next;
      index += 1;
      continue;
    }

    if (arg === "--manual") {
      parsed.manual = true;
      continue;
    }

    if ((arg === "--ad" || arg === "--marker" || arg === "-m") && next) {
      parsed.markers.push(next);
      index += 1;
      continue;
    }

    if (!parsed.sellerUrl && isSellerHomeUrl(arg)) {
      parsed.sellerUrl = arg;
      continue;
    }

    if (!parsed.searchUrl && /^https?:\/\//i.test(arg)) {
      parsed.searchUrl = arg;
      continue;
    }

    parsed.markers.push(arg);
  }

  return parsed;
}

function toMarker(raw) {
  return {
    raw,
    normalizedText: normalizeText(raw),
    normalizedUrl: looksLikeUrl(raw) ? normalizeUrl(raw) : "",
    id: extractOlxId(raw),
    matchTitle: true,
  };
}

function listingToMarker(listing) {
  return {
    raw: listing.id || listing.url || listing.title,
    title: listing.title,
    url: listing.url,
    normalizedText: normalizeText(listing.title),
    normalizedUrl: normalizeUrl(listing.url),
    id: listing.id,
    matchTitle: false,
  };
}

async function getSellerUrl() {
  if (cli.sellerUrl) return cli.sellerUrl;

  try {
    const file = await fs.readFile(DEFAULT_SELLER_FILE, "utf8");
    return file
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line && !line.startsWith("#")) || "";
  } catch (error) {
    if (error.code === "ENOENT") return "";
    throw error;
  }
}

async function saveSellerCache(sellerUrl, listings) {
  const lines = [
    "# Автоматично зібрано зі сторінки продавця.",
    `# Джерело: ${sellerUrl}`,
    `# Оновлено: ${new Date().toISOString()}`,
    "# Формат: ID | Назва | URL",
    "",
    ...listings.map((listing) => `${listing.id || "-"} | ${listing.title || "Без назви"} | ${listing.url}`),
    "",
  ];

  await fs.writeFile(SELLER_CACHE_FILE, lines.join("\n"), "utf8");
}

async function fetchHtml(url) {
  const response = await fetch(url, {
    headers: REQUEST_HEADERS,
    redirect: "follow",
  });

  if (!response.ok) {
    throw new Error(`OLX повернув HTTP ${response.status}`);
  }

  return response.text();
}

function parseListings(html, baseUrl) {
  const cards = extractCardHtml(html);
  const listings = cards
    .map((cardHtml) => parseCard(cardHtml, baseUrl))
    .filter(Boolean);

  const fallbackListings = listings.length ? listings : parseLinksFallback(html, baseUrl);
  const unique = [];
  const seen = new Set();

  for (const listing of fallbackListings) {
    const key = listing.id || listing.url;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push({
      ...listing,
      position: unique.length + 1,
    });
  }

  return unique;
}

function extractCardHtml(html) {
  const markerRegex = /data-cy=["']l-card["']/gi;
  const starts = [];
  let match;

  while ((match = markerRegex.exec(html))) {
    const divStart = html.lastIndexOf("<div", match.index);
    starts.push(divStart >= 0 ? divStart : match.index);
  }

  return starts.map((start, index) => {
    const end = starts[index + 1] || html.length;
    return html.slice(start, end);
  });
}

function parseCard(cardHtml, baseUrl) {
  const link = findAdLink(cardHtml, baseUrl);
  if (!link) return null;

  const title =
    extractByRegex(cardHtml, /data-cy=["']ad-card-title["'][^>]*>([\s\S]*?)<\/[^>]+>/i) ||
    extractByRegex(cardHtml, /<h[1-6][^>]*>([\s\S]*?)<\/h[1-6]>/i) ||
    extractAttribute(cardHtml, "alt") ||
    extractAttribute(cardHtml, "title") ||
    "";

  return {
    title: cleanText(title),
    url: link,
    id: extractOlxId(link),
  };
}

function parseLinksFallback(html, baseUrl) {
  const listings = [];
  const linkRegex = /<a\b[^>]*href=(["'])(.*?)\1[^>]*>([\s\S]*?)<\/a>/gi;
  let match;

  while ((match = linkRegex.exec(html))) {
    const url = toAbsoluteUrl(decodeHtml(match[2]), baseUrl);
    if (!isAdUrl(url)) continue;

    listings.push({
      title: cleanText(match[3]),
      url,
      id: extractOlxId(url),
    });
  }

  return listings;
}

function findAdLink(html, baseUrl) {
  const linkRegex = /<a\b[^>]*href=(["'])(.*?)\1/gi;
  let match;

  while ((match = linkRegex.exec(html))) {
    const url = toAbsoluteUrl(decodeHtml(match[2]), baseUrl);
    if (isAdUrl(url)) return url;
  }

  return "";
}

function isAdUrl(url) {
  return /olx\.ua\/d\//i.test(url) && /\.html(?:[?#].*)?$/i.test(url);
}

function findMatches(listings, markers) {
  const matches = [];

  for (const listing of listings) {
    const normalizedListingUrl = normalizeUrl(listing.url);
    const normalizedListingTitle = normalizeText(listing.title);

    const matchedBy = markers.filter((marker) => {
      if (marker.id && listing.id && marker.id.toLowerCase() === listing.id.toLowerCase()) {
        return true;
      }

      if (
        marker.normalizedUrl &&
        (normalizedListingUrl === marker.normalizedUrl ||
          normalizedListingUrl.includes(marker.normalizedUrl) ||
          marker.normalizedUrl.includes(normalizedListingUrl))
      ) {
        return true;
      }

      return (
        marker.matchTitle !== false &&
        marker.normalizedText.length >= 3 &&
        normalizedListingTitle.includes(marker.normalizedText)
      );
    });

    if (matchedBy.length) {
      matches.push({ ...listing, matchedBy: matchedBy.map((marker) => marker.raw) });
    }
  }

  return matches;
}

function printReport({ listings, matches, markers }) {
  console.log(`Усього оголошень на сторінці: ${listings.length}`);
  console.log(`Твоїх оголошень знайдено: ${matches.length}`);
  console.log("");

  if (matches.length) {
    for (const match of matches) {
      console.log(`#${match.position} - ${match.title || "Без назви"}`);
      console.log(`   ${match.url}`);
      console.log(`   Збіг: ${match.matchedBy.join(" | ")}`);
    }
  } else {
    console.log("Твої оголошення на цій сторінці не знайдені.");
    console.log("Перевір, чи в my-ads.txt вписані точні посилання, ID або достатньо унікальні частини назв.");
  }

  const unmatched = markers.filter(
    (marker) => !matches.some((match) => match.matchedBy.includes(marker.raw)),
  );

  if (unmatched.length) {
    console.log("");
    console.log("Не знайшов на сторінці пошуку такі твої оголошення:");
    for (const marker of unmatched) {
      console.log(`- ${marker.raw}`);
    }
  }
}

function extractByRegex(html, regex) {
  const match = html.match(regex);
  return match ? match[1] : "";
}

function extractAttribute(html, name) {
  const regex = new RegExp(`${name}=(["'])(.*?)\\1`, "i");
  const match = html.match(regex);
  return match ? match[2] : "";
}

function toAbsoluteUrl(url, baseUrl) {
  try {
    return new URL(url, baseUrl).toString();
  } catch {
    return url;
  }
}

function looksLikeUrl(value) {
  return /^https?:\/\//i.test(value);
}

function isSellerHomeUrl(value) {
  if (!looksLikeUrl(value)) return false;

  try {
    const url = new URL(value);
    return url.hostname.endsWith(".olx.ua") && /\/(?:uk\/)?home\/?$/i.test(url.pathname);
  } catch {
    return false;
  }
}

function normalizeUrl(value) {
  try {
    const url = new URL(value);
    url.hash = "";
    const search = new URLSearchParams(url.search);
    for (const key of [...search.keys()]) {
      if (/^(utm_|search|reason|ref)/i.test(key)) search.delete(key);
    }
    url.search = search.toString();
    return url.toString().replace(/\/+$/, "").toLowerCase();
  } catch {
    return value.trim().replace(/\/+$/, "").toLowerCase();
  }
}

function extractOlxId(value) {
  const match = value.match(/\bID([a-z0-9]+)(?:\.html)?\b/i);
  return match ? `ID${match[1]}` : "";
}

function normalizeText(value) {
  return decodeHtml(value)
    .toLowerCase()
    .replace(/<[^>]+>/g, " ")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanText(value) {
  return decodeHtml(value)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function decodeHtml(value) {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ");
}

main().catch((error) => {
  console.error("Помилка:", error.message);
  process.exitCode = 1;
});
