import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

# Regexy do wyciągania danych
PRICE_RE = re.compile(r"(\d{1,4},\d{2})\s*zł(\+)?", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

SKIP_EXACT = {
    "Zapisz termin",
    "Pokaż wszystkie zdjęcia",
    "Zarezerwuj",
    "Zarezerwuj wizytę",
}

STOP_AT = {"Opinie", "Udogodnienia"}

KNOWN_CATEGORIES = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Zabiegi regenerujące włosy",
    "Prostowanie",
    "Fryzura okolicznościowa",
    "Trwała ondulacja",
}

GROUPABLE_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja",
    "Koloryzacja włosów",
    "Farbowanie-odrost",
    "Metamorfoza koloru",
    "Przyciemnianie koloru blond+strzyżenie",
    "Tonowanie włosów + strzyżenie damskie",
    "Rozjaśnianie - odrost",
    "Refleksy/sombre",
    "Dekoloryzacja włosów",
    "Baleyage",
    "Regeneracja włosów-botox",
    "Silna regeneracja włosów-PRO REPAIR COMBO+SPA",
    "Nanoplastia",
    "Trwała ondulacja",
}

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm_key(s: str) -> str:
    return clean(s).lower()

def is_price_line(s: str) -> bool:
    return bool(PRICE_RE.search(s))

def extract_price(s: str) -> Optional[str]:
    m = PRICE_RE.search(s)
    if not m:
        return None
    val = m.group(1)
    plus = m.group(2)
    return f"{val} zł{plus or ''}"

def is_duration_line(s: str) -> bool:
    return bool(DUR_RE.search(s))

def normalize_duration(s: str) -> str:
    if not s:
        return ""
    m = DUR_RE.search(s)
    if not m:
        return clean(s)
    d = m.group(1).lower().replace(" ", "")
    d = re.sub(r"(\d+)g(\d+)min", r"\1g \2min", d)
    return d

def is_count_line(s: str) -> bool:
    return bool(re.fullmatch(r"\d+\s+usług[iy]?", s))

def is_noise_line(s: str) -> bool:
    if not s:
        return True
    if s in SKIP_EXACT:
        return True
    if s.lower().startswith("portfolio usługi") or s.lower().startswith("image:"):
        return True
    return False

def looks_like_hair_variant(name: str) -> bool:
    n = norm_key(name)
    keywords = ["włosy", "wlosy", "wariant", "metamorfoza", "odrost", "short", "long", "ramion", "linii uszu"]
    return any(k in n for k in keywords)

def find_section(lines: List[str]) -> List[str]:
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi":
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    end = None
    for j in range(start + 1, len(lines)):
        if lines[j] in STOP_AT:
            end = j
            break
    return lines[start : (end or len(lines))]

def split_categories(sec: List[str]) -> List[Tuple[str, int, int]]:
    starts: List[Tuple[str, int]] = []
    i = 0
    while i < len(sec):
        line = sec[i]
        if line in STOP_AT:
            break
        if line in KNOWN_CATEGORIES or (i + 1 < len(sec) and is_count_line(sec[i + 1])):
            name = line
            j = i + 1
            if j < len(sec) and is_count_line(sec[j]):
                j += 1
            starts.append((name, j))
            i = j
            continue
        i += 1

    out: List[Tuple[str, int, int]] = []
    for idx, (name, s) in enumerate(starts):
        e = starts[idx + 1][1] - 1 if idx + 1 < len(starts) else len(sec)
        out.append((name, s, e))
    return out

def try_parse_bookable_entry(lines: List[str], i: int) -> Optional[Tuple[Dict[str, Any], int]]:
    title = lines[i]
    if is_noise_line(title) or title == "Umów" or is_price_line(title) or is_duration_line(title) or is_count_line(title):
        return None

    price: Optional[str] = None
    dur: str = ""
    j = i + 1
    limit = min(i + 12, len(lines) - 1)

    while j <= limit:
        s = lines[j]
        if s in STOP_AT or s in KNOWN_CATEGORIES:
            return None
        if is_noise_line(s):
            j += 1
            continue
        if price is None and s not in {"Umów"} and not is_price_line(s) and not is_duration_line(s) and not is_count_line(s):
            return None
        if price is None and is_price_line(s):
            price = extract_price(s)
        if not dur and is_duration_line(s):
            dur = normalize_duration(s)
        if s == "Umów":
            if price:
                return {"name": clean(title), "price": price, "duration": dur}, j + 1
            return None
        j += 1
    return None

def parse_category(lines: List[str]) -> List[Dict[str, Any]]:
    items_out: List[Dict[str, Any]] = []
    current_group: Optional[Dict[str, Any]] = None

    def flush_group():
        nonlocal current_group
        if current_group:
            current_group["variants"] = [v for v in current_group["variants"] if v.get("price")]
            if current_group["variants"]:
                items_out.append(current_group)
        current_group = None

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or is_noise_line(line) or line == "Umów" or is_count_line(line):
            i += 1
            continue

        parsed = try_parse_bookable_entry(lines, i)
        if parsed:
            entry, next_i = parsed
            title = entry["name"]

            if current_group:
                if looks_like_hair_variant(title):
                    current_group["variants"].append(entry)
                    i = next_i
                    continue
                else:
                    flush_group()

            if title in GROUPABLE_SERVICES:
                current_group = {"name": title, "variants": []}
                if looks_like_hair_variant(title):
                    current_group["variants"].append(entry)
                i = next_i
                continue

            items_out.append(entry)
            i = next_i
        else:
            if line in GROUPABLE_SERVICES:
                flush_group()
                current_group = {"name": line, "variants": []}
            i += 1
    flush_group()
    return items_out

def parse() -> Dict[str, Any]:
    r = requests.get(BOOKSY_URL, headers={"User-Agent": "SeiBelloPriceBot/1.0"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    raw_lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in raw_lines if x and x not in SKIP_EXACT]
    sec = find_section(lines)

    categories_out: List[Dict[str, Any]] = []
    for cat_name, s, e in split_categories(sec):
        items = parse_category(sec[s:e])
        if items:
            categories_out.append({"name": cat_name, "items": items})

    return {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories_out,
    }

if __name__ == "__main__":
    data = parse()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Zaktualizowano: {OUT_PATH}")
