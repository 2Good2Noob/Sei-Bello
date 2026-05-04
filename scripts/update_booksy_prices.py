import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = Path("data/booksy-prices.json")

# 185,00 zł   / 250,00 zł+   (czasem NBSP)
PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)
# 30min / 1g / 1g 30min / 4g 30min (czasem bez spacji)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

# "11 usług" / "1 usługa"
COUNT_RE = re.compile(r"^\d+\s+usług(?:a|i|y)?$", re.IGNORECASE)

SKIP_EXACT = {
    "Zarezerwuj", "Zapisz termin", "Pokaż wszystkie zdjęcia",
    "Wybierz usługę", "Szukaj usługi",
}

SECTION_START = "Usługi"
SECTION_END_MARKERS = ("Udogodnienia", "Opinie")  # utnij zanim zacznie się reszta strony
PRIMARY_CATEGORY_FALLBACK = "Cennik"

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line))

def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line))

def looks_like_portfolio_noise(line: str) -> bool:
    l = line.lower()
    return ("portfolio usługi" in l) or l.startswith("portfolio") or l.startswith("image:")

def is_variant_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return (
        n.startswith("włosy") or n.startswith("wlosy") or
        n.startswith("wariant") or
        n.startswith("średnie") or n.startswith("srednie") or
        n.startswith("długie") or n.startswith("dlugie") or
        n.startswith("krótkie") or n.startswith("krotkie")
    )

def extract_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    out = []
    for x in lines:
        if not x:
            continue
        if x in SKIP_EXACT:
            continue
        # odfiltruj typowe śmieci z altów
        if looks_like_portfolio_noise(x):
            continue
        # separator, który czasem się pojawia w ekstrakcji
        if x in {"* * *", "***"}:
            continue
        out.append(x)
    return out

def find_section(lines: list[str]) -> list[str]:
    try:
        start = lines.index(SECTION_START)
    except ValueError:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    end = None
    for marker in SECTION_END_MARKERS:
        try:
            idx = lines.index(marker, start + 1)
            end = idx if end is None else min(end, idx)
        except ValueError:
            pass

    if end is None:
        end = len(lines)

    return lines[start + 1 : end]  # bez samego nagłówka "Usługi"

def parse_card(card_lines: list[str], current_category: str | None) -> dict | None:
    """
    card_lines: wszystko pomiędzy poprzednim 'Umów' a bieżącym 'Umów'
    """
    # wyczyść z liczników typu "11 usług" itp.
    lines = [x for x in card_lines if x and not COUNT_RE.match(x)]
    if not lines:
        return None

    # znajdź pierwszą linię z ceną
    price_idx = None
    for i, l in enumerate(lines):
        if is_price(l):
            price_idx = i
            break
    if price_idx is None:
        return None  # karta bez ceny -> ignoruj (np. czyste nagłówki)

    # nazwa: weź pierwszą sensowną linię przed ceną, ale nie równą nazwie kategorii
    name_candidates = []
    for l in lines[:price_idx]:
        if is_price(l) or is_duration(l):
            continue
        if l in {SECTION_START, *SECTION_END_MARKERS}:
            continue
        if current_category and clean(l).lower() == clean(current_category).lower():
            continue
        # często pierwsza linia to właściwa nazwa usługi
        name_candidates.append(l)

    if not name_candidates:
        return None

    name = name_candidates[0].strip()

    # cena (jeśli w jednej linii jest kilka – bierz ostatnią)
    price_line = lines[price_idx]
    prices = PRICE_RE.findall(price_line)
    price = prices[-1] if prices else clean(price_line)

    # czas: pierwszy znaleziony po cenie (albo gdziekolwiek dalej)
    duration = ""
    for l in lines[price_idx + 1 : price_idx + 15]:
        if is_duration(l):
            duration = clean(l)
            break
    if not duration:
        for l in lines:
            if is_duration(l):
                duration = clean(l)
                break

    return {"name": clean(name), "price": clean(price), "duration": duration}

def group_items(raw_items: list[dict]) -> list[dict]:
    """
    Grupuje warianty typu:
      Koloryzacja włosów  (nagłówek)
        Włosy krótkie...
        Włosy długie...
    oraz scala duplikaty nazw obok siebie (np. Farbowanie-odrost x2) w variants.
    """
    out: list[dict] = []
    i = 0

    def norm_name(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    while i < len(raw_items):
        item = raw_items[i]
        name = item.get("name", "")
        price = item.get("price", "")
        duration = item.get("duration", "")

        nxt = raw_items[i + 1] if i + 1 < len(raw_items) else None

        # 1) Grupowanie "nagłówek + warianty"
        if nxt and (not is_variant_name(name)) and is_variant_name(nxt.get("name", "")):
            group = {"name": name, "variants": []}

            # jeśli nagłówek ma cenę, dodaj jako "Cena od" TYLKO jeśli nie powtarza wariantu
            if price:
                group["variants"].append({"name": "Cena od", "price": price, "duration": duration})

            # zbierz warianty
            i += 1
            while i < len(raw_items) and is_variant_name(raw_items[i].get("name", "")):
                v = raw_items[i]
                # tylko warianty z ceną
                if v.get("price"):
                    group["variants"].append({
                        "name": v.get("name", ""),
                        "price": v.get("price", ""),
                        "duration": v.get("duration", ""),
                    })
                i += 1

            # usuń "Cena od" jeśli dubluje się z wariantem (ta sama cena)
            if group["variants"]:
                seen_prices = set()
                cleaned_variants = []
                for v in group["variants"]:
                    p = v.get("price", "")
                    key = (norm_name(v.get("name", "")), p)
                    # deduplikuj identyczne (nazwa+cena)
                    if key in seen_prices:
                        continue
                    seen_prices.add(key)
                    cleaned_variants.append(v)
                group["variants"] = cleaned_variants

            # jeśli po filtrach nic nie zostało, pomiń
            if group.get("variants"):
                out.append(group)
            continue  # i już jest na następnym nie-wariancie

        # 2) Duplikaty nazw obok siebie -> variants "Wariant 1/2/..."
        if nxt and norm_name(nxt.get("name", "")) == norm_name(name):
            group = {"name": name, "variants": []}
            k = 1
            while i < len(raw_items) and norm_name(raw_items[i].get("name", "")) == norm_name(name):
                v = raw_items[i]
                if v.get("price"):
                    group["variants"].append({
                        "name": f"Wariant {k}",
                        "price": v.get("price", ""),
                        "duration": v.get("duration", ""),
                    })
                    k += 1
                i += 1
            if group["variants"]:
                out.append(group)
            continue

        # 3) Normalna pozycja – tylko jeśli ma cenę
        if price:
            out.append({"name": name, "price": price, "duration": duration})

        i += 1

    return out

def parse_booksy() -> dict:
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    lines = extract_lines(r.text)
    sec = find_section(lines)

    categories: "OrderedDict[str, list[dict]]" = OrderedDict()
    current_cat = PRIMARY_CATEGORY_FALLBACK

    def ensure_cat(name: str):
        nonlocal current_cat
        current_cat = name
        if current_cat not in categories:
            categories[current_cat] = []

    buf: list[str] = []

    i = 0
    while i < len(sec):
        line = sec[i]
        next_line = sec[i + 1] if i + 1 < len(sec) else ""

        # wykryj kategorię:
        if clean(line).lower() == "popularne usługi":
            ensure_cat("Popularne usługi")
            # nie wrzucaj do bufora karty
            i += 1
            continue

        if next_line and COUNT_RE.match(clean(next_line)):
            ensure_cat(clean(line))
            i += 2
            continue

        # koniec karty usługi
        if line == "Umów":
            card = parse_card(buf, current_cat)
            if card:
                categories.setdefault(current_cat, []).append(card)
            buf = []
            i += 1
            continue

        # normalna linia do bufora
        buf.append(line)
        i += 1

    # jeśli coś zostało w buforze – zwykle śmieci, ignorujemy

    # post-process: grupuj warianty + filtruj puste
    out_cats = []
    for cat_name, raw_items in categories.items():
        grouped = group_items(raw_items)
        if grouped:
            out_cats.append({"name": cat_name, "items": grouped})

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": out_cats,
    }
    return payload

if __name__ == "__main__":
    data = parse_booksy()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(c["items"]) for c in data["categories"])
    print(f"OK -> {OUT_PATH} ({total} pozycji)")
