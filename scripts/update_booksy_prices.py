import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

# ceny typu: 185,00 zł / 250,00 zł+
PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)

# czasy typu: 1g / 1g 30min / 30min / 2 g 30 min
DUR_RE = re.compile(
    r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min|\d+\s*godz\.?(?:\s*\d+\s*min)?)\b",
    re.IGNORECASE,
)

# warianty typu: "Włosy ...", "Wariant 1"
VARIANT_LABEL_RE = re.compile(r"^(włosy|wlosy|wariant)\b", re.IGNORECASE)

# używamy do rozpoznania "nagłówka grupy" (np. Koloryzacja włosów / Regeneracja włosów ...)
GROUP_HEADER_RE = re.compile(r"\bwłosów\b|\bwlosow\b", re.IGNORECASE)

# rzeczy które w tekście Booksy są "śmieciem" dla parsowania
SKIP_EXACT = {
    "Umów",
    "Zarezerwuj",
    "Zapisz termin",
    "Pokaż wszystkie zdjęcia",
    "Zdjęcia",
    "Usługi",
    "Opinie",
}
SKIP_CONTAINS = (
    "Zaoszczędź",
    "Oszczędź",
    "Promocja",
    "Nowość",
    "Nowosc",
    "zł do",
    "do -",
)

# opcjonalnie: ręczne nazwy kategorii (gdy Booksy nie ma linijki "X usług")
KNOWN_CATS = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Prostowanie",
    "Fryzury okolicznościowe",
    "Fryzura okolicznościowa",
    "Trwała ondulacja",
    "Zabiegi regenerujące włosy",
    "Zabiegi regenerujące",
}


def clean(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line or ""))


def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line or ""))


def is_variant_label(line: str) -> bool:
    return bool(VARIANT_LABEL_RE.match((line or "").strip()))


def normalize_price(line: str) -> str:
    """
    Jeśli w jednej linijce są 2 ceny (np. stara i nowa),
    zwróć "stara nowa", żeby front mógł pokazać przekreślenie.
    """
    line = clean(line)
    prices = PRICE_RE.findall(line)
    if not prices:
        return line
    if len(prices) >= 2:
        return f"{prices[0]} {prices[-1]}"
    return prices[-1]


def normalize_duration(line: str) -> str:
    """
    Ujednolica zapisy:
    - "1 godz. 30 min" -> "1g 30min"
    - "30 min" -> "30min"
    - usuwa zbędne spacje
    """
    line = clean(line).lower()
    line = line.replace("godz.", "g").replace("godz", "g")
    # ujednolicenie spacji w "2 g 30 min"
    line = re.sub(r"(\d)\s*g\b", r"\1g", line)
    line = re.sub(r"(\d)\s*min\b", r"\1min", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def should_skip(line: str) -> bool:
    if not line:
        return True
    if line in SKIP_EXACT:
        return True
    low = line.lower()
    for s in SKIP_CONTAINS:
        if s.lower() in low:
            return True
    return False


def find_section(lines: list[str]) -> list[str]:
    """
    Szukamy zakresu od "Usługi" do "Opinie".
    """
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi" and any("Popularne usługi" in x for x in lines[i : i + 200]):
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] == "Opinie":
            end = j
            break

    return lines[start : end or len(lines)]


def assign_last(items: list[dict], field: str, value: str) -> bool:
    """
    Przypisz value do ostatniego elementu w items, który ma puste pole `field`.
    """
    for it in reversed(items):
        if not it.get(field):
            it[field] = value
            return True
    return False


def fix_groups(items: list[dict]) -> list[dict]:
    """
    Naprawia typowy problem Booksy:
    - "Koloryzacja włosów" dostaje cenę, a warianty "Włosy ..." mają ceny przesunięte.
    Zasada:
    - jeśli element zawiera "włosów" i po nim idą warianty ("Włosy ..."/"Wariant ..."),
      to traktujemy go jako nagłówek grupy i przesuwamy ceny/czasy o 1 w dół:
        base.price -> wariant1
        wariant1.price -> wariant2
        ...
      a base.price/base.duration czyścimy.
    """
    out = []
    i = 0
    while i < len(items):
        base = items[i]
        base_name = clean(base.get("name", ""))
        if not base_name:
            i += 1
            continue

        # warunek startu grupy:
        # 1) base zawiera "włosów" / "wlosow"
        # 2) następny item to wariant label
        if (
            GROUP_HEADER_RE.search(base_name)
            and i + 1 < len(items)
            and is_variant_label(items[i + 1].get("name", ""))
        ):
            # zbierz warianty
            variants = []
            j = i + 1
            while j < len(items) and is_variant_label(items[j].get("name", "")):
                variants.append(items[j])
                j += 1

            # sekwencje cen/czasów (zachowaj puste)
            price_seq = [clean(base.get("price", ""))] + [clean(v.get("price", "")) for v in variants]
            dur_seq = [clean(base.get("duration", ""))] + [clean(v.get("duration", "")) for v in variants]

            # przesunięcie w dół o 1:
            # wariant[k] dostaje price_seq[k] (czyli base -> v0, v0 -> v1, ...)
            for k, v in enumerate(variants):
                if k < len(price_seq) and price_seq[k]:
                    v["price"] = price_seq[k]
                # jeśli brakuje - zostaw jak jest (może być "")
                if k < len(dur_seq) and dur_seq[k]:
                    v["duration"] = dur_seq[k]

            # base czyścimy (żeby front użył go jako nagłówka)
            base["price"] = ""
            base["duration"] = ""

            out.append(base)
            out.extend(variants)
            i = j
            continue

        out.append(base)
        i += 1

    return out


def parse() -> dict:
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    raw_lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in raw_lines if x and not should_skip(x)]

    sec = find_section(raw_lines)  # tu celowo raw, bo find_section szuka "Usługi"/"Opinie"
    sec = [clean(x) for x in sec if clean(x) and not should_skip(clean(x))]

    categories = []
    current = {"name": "Cennik", "items": []}
    categories.append(current)

    def start_category(name: str):
        nonlocal current
        current = {"name": name, "items": []}
        categories.append(current)

    i = 0
    while i < len(sec):
        line = sec[i]

        # Kategoria typu: "Usługi męskie" + "4 usługi"
        if i + 1 < len(sec) and re.fullmatch(r"\d+\s+usług[iy]?", sec[i + 1]):
            start_category(line)
            i += 2
            continue

        # Kategoria bez licznika (czasem Booksy tak zwraca)
        if line in KNOWN_CATS:
            start_category(line)
            i += 1
            continue

        # --- NOWY mechanizm: każda linia tekstu to potencjalna pozycja,
        # a ceny/czasy "doklejają się" do ostatniej pozycji bez tych pól.
        if is_price(line):
            price = normalize_price(line)
            assign_last(current["items"], "price", price)
            i += 1
            continue

        if is_duration(line):
            dur = normalize_duration(line)
            assign_last(current["items"], "duration", dur)
            i += 1
            continue

        # zwykły tekst -> pozycja (także warianty bez ceny/czasu)
        current["items"].append({"name": line, "price": "", "duration": ""})
        i += 1

    # post-process: usuń puste i napraw grupy
    fixed_categories = []
    for c in categories:
        items = [it for it in c["items"] if clean(it.get("name", ""))]
        if not items:
            continue

        items = fix_groups(items)

        # drobna kosmetyka: usuń duplikaty 1:1 (czasem Booksy powtarza)
        dedup = []
        seen = set()
        for it in items:
            key = (clean(it.get("name", "")), clean(it.get("price", "")), clean(it.get("duration", "")))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(it)

        fixed_categories.append({"name": c["name"], "items": dedup})

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": fixed_categories,
    }
    return payload


if __name__ == "__main__":
    data = parse()
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(len(c["items"]) for c in data["categories"])
    print(f"OK -> {OUT_PATH} ({total} pozycji)")
