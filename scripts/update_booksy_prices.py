import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)
# łapie: "30min", "1g", "1g 30min", "2g 30min", "1g30min"
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min|\d+g\d+\s*min|\d+g\d+min)\b", re.IGNORECASE)

COUNT_RE = re.compile(r"^\d+\s+usług[iy]?$", re.IGNORECASE)

SKIP_EXACT = {
    "Umów", "Zarezerwuj", "Zapisz termin", "Pokaż wszystkie zdjęcia",
    "Pokaż więcej", "Zobacz więcej", "Zobacz wszystkie", "rozwiń", "Rozwiń",
}

STOP_MARKERS = {
    "opinie",
    "udogodnienia",
    "pracownicy",
    "portfolio",
    "o nas",
}

# WARIANTY: tylko naprawdę „wariantowe” etykiety (żeby nie zlepiać różnych usług w jedną)
VARIANT_PREFIX = re.compile(r"^(cena od|od\b|włosy|wlosy|wariant)\b", re.IGNORECASE)

KNOWN_CATEGORIES = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Prostowanie",
    "Fryzura okolicznościowa",
    "Fryzury okolicznościowe",
    "Trwała ondulacja",
    "Zabiegi regenerujące włosy",
    "Zabiegi regenerujące",
}

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm_duration(s: str) -> str:
    s = clean(s)
    # "1g30min" -> "1g 30min"
    s = re.sub(r"(\d)g(\d)", r"\1g \2", s, flags=re.IGNORECASE)
    # "1 g" -> "1g"
    s = re.sub(r"(\d)\s*g\b", r"\1g", s, flags=re.IGNORECASE)
    # "30 min" -> "30min"
    s = re.sub(r"(\d)\s*min\b", r"\1min", s, flags=re.IGNORECASE)
    return s

def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line))

def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line))

def is_stop(line: str) -> bool:
    return clean(line).lower() in STOP_MARKERS

def is_variant_title(line: str) -> bool:
    return bool(VARIANT_PREFIX.match(clean(line)))

def find_section(lines: list[str]) -> list[str]:
    # start: od "Usługi" (tam jest cennik)
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi":
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    # end: zanim zacznie się Opinie/Udogodnienia itd.
    end = None
    for j in range(start + 1, len(lines)):
        low = lines[j].lower()
        if low in STOP_MARKERS:
            end = j
            break
    return lines[start:end or len(lines)]

def has_price_ahead(lines: list[str], i: int, window: int = 10) -> bool:
    for j in range(i + 1, min(i + 1 + window, len(lines))):
        if is_price(lines[j]):
            return True
    return False

def parse_price_block(lines: list[str], title_idx: int):
    """
    Czyta pojedynczy blok: TITLE -> (cena) -> (czas) -> (Umów)
    Zwraca: (obj, next_index)
    """
    title = clean(lines[title_idx])
    price = ""
    dur = ""

    # znajdź pierwszą cenę po tytule
    p_idx = None
    for j in range(title_idx + 1, min(title_idx + 25, len(lines))):
        if is_price(lines[j]):
            p_idx = j
            break
        # jeśli trafimy na ewidentny kolejny tytuł bez ceny, odpuść
        if j > title_idx + 2 and not is_duration(lines[j]) and not is_price(lines[j]) and lines[j] not in SKIP_EXACT and has_price_ahead(lines, j, 6):
            break

    if p_idx is None:
        return {"name": title, "price": "", "duration": ""}, title_idx + 1

    # cena: jeśli jest kilka, bierz ostatnią (po promce)
    prices = PRICE_RE.findall(lines[p_idx])
    price = clean(prices[-1]) if prices else clean(lines[p_idx])

    # czas: pierwszy duration w pobliżu ceny
    for j in range(p_idx + 1, min(p_idx + 10, len(lines))):
        if is_duration(lines[j]):
            dur = norm_duration(lines[j])
            break
        if clean(lines[j]).lower() in {"umów", "umow"}:
            break

    # przewiń do "Umów" jeśli jest
    nxt = p_idx + 1
    for j in range(p_idx + 1, min(p_idx + 40, len(lines))):
        if clean(lines[j]).lower() in {"umów", "umow"}:
            nxt = j + 1
            break

    return {"name": title, "price": price, "duration": dur}, nxt

def parse():
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x and x not in SKIP_EXACT]

    sec = find_section(lines)

    categories = []
    current = None

    def start_category(name: str):
        nonlocal current
        current = {"name": name, "items": []}
        categories.append(current)

    i = 0
    while i < len(sec):
        line = clean(sec[i])

        if not line or is_stop(line):
            break

        # kategoria: "Usługi męskie" + "4 usługi"
        if i + 1 < len(sec) and COUNT_RE.match(clean(sec[i + 1])):
            start_category(line)
            i += 2
            continue

        # kategorie bez licznika / niestandardowe
        if line in KNOWN_CATEGORIES:
            if current is None or current["name"] != line:
                start_category(line)
            i += 1
            continue

        # pomiń liczniki jeśli trafią w środku
        if COUNT_RE.match(line):
            i += 1
            continue

        # jeśli nie mamy kategorii, pomiń do pierwszej
        if current is None:
            i += 1
            continue

        # potencjalny tytuł usługi
        if not is_price(line) and not is_duration(line) and line not in SKIP_EXACT and has_price_ahead(sec, i, 12):
            base, j = parse_price_block(sec, i)

            # warianty tylko jeśli bezpośrednio po bazie idą linie typu "Włosy..." / "Wariant..." / "Cena od..."
            variants = []
            k = j
            while k < len(sec):
                nxt = clean(sec[k])
                if not nxt or is_stop(nxt):
                    break
                if nxt in KNOWN_CATEGORIES or (k + 1 < len(sec) and COUNT_RE.match(clean(sec[k + 1]))):
                    break

                if is_variant_title(nxt) and has_price_ahead(sec, k, 10):
                    v, k2 = parse_price_block(sec, k)
                    if v["price"] or v["duration"]:
                        variants.append(v)
                    k = k2
                    continue

                break

            # buduj item
            if variants:
                # bazę dawaj jako "Cena od" (żeby front mógł ją dać do nagłówka)
                base_variant = {"name": "Cena od", "price": base["price"], "duration": base["duration"]}
                item = {"name": base["name"], "variants": [base_variant] + variants}
            else:
                item = base

            # nie zapisuj totalnych śmieci (puste wszystko)
            if ("variants" in item and any(v.get("price") or v.get("duration") for v in item["variants"])) or item.get("price") or item.get("duration"):
                current["items"].append(item)

            i = k if variants else j
            continue

        i += 1

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c.get("items")],
    }
    return payload

if __name__ == "__main__":
    data = parse()
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = sum(
        len(c["items"]) for c in data["categories"]
    )
    print(f"OK -> {OUT_PATH} ({total} pozycji)")
