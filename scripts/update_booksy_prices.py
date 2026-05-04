import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)
COUNT_RE = re.compile(r"^\d+\s+usług[iy]?$", re.IGNORECASE)

# Tego NIE wyrzucamy: "Umów" jest potrzebne do parsowania wariantów
SKIP_TEXT = {
    "Zarezerwuj", "Zapisz termin", "Pokaż wszystkie zdjęcia",
    "Wstecz", "Dalej", "Pokaż więcej", "Pokaż mniej",
}

HEADING_TAG_RE = re.compile(r"^h[1-6]$")

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def is_price(s: str) -> bool:
    return bool(PRICE_RE.search(s))

def is_duration(s: str) -> bool:
    return bool(DUR_RE.search(s))

def extract_price(s: str) -> str:
    # jeśli w linii są 2 ceny (promo), weź ostatnią jako aktualną
    prices = PRICE_RE.findall(s)
    return clean(prices[-1]) if prices else clean(s)

def normalize_duration(s: str) -> str:
    return clean(s).replace(" ", "")

def is_heading_tag(t: Any) -> bool:
    return isinstance(t, Tag) and bool(HEADING_TAG_RE.match(t.name or ""))

def find_heading(soup: BeautifulSoup, title: str) -> Optional[Tag]:
    title = clean(title).lower()
    for h in soup.find_all(HEADING_TAG_RE):
        if clean(h.get_text()).lower() == title:
            return h
    return None

def collect_strings_between(start: Tag, end: Tag) -> List[str]:
    out: List[str] = []
    last = None
    for el in start.next_elements:
        if el == end:
            break
        if isinstance(el, NavigableString):
            s = clean(str(el))
            if not s:
                continue
            if s in SKIP_TEXT:
                continue
            # usuń “puste” ozdobniki
            if s in {"…", "..."}:
                continue
            if s == last:
                continue
            out.append(s)
            last = s
    return out

def parse_rows_from_block(lines: List[str]) -> List[Dict[str, str]]:
    """
    Z bloku jednej usługi wyciąga wiersze po "Umów":
    [nazwa] [cena] [czas] Umów
    """
    rows: List[Dict[str, str]] = []

    def is_book_button(x: str) -> bool:
        return x.lower() == "umów" or x.lower().startswith("umów")

    for i, t in enumerate(lines):
        if not is_book_button(t):
            continue

        # cena najbliżej wstecz
        p_idx = None
        price = ""
        for j in range(i - 1, max(-1, i - 25), -1):
            if is_price(lines[j]):
                p_idx = j
                price = extract_price(lines[j])
                break
        if p_idx is None or not price:
            continue

        # czas (szukaj blisko ceny aż do "Umów")
        dur = ""
        for k in range(p_idx + 1, i):
            if is_duration(lines[k]):
                dur = normalize_duration(lines[k])
        if not dur:
            # czas czasem bywa przed ceną
            for k in range(p_idx - 1, max(-1, p_idx - 8), -1):
                if is_duration(lines[k]):
                    dur = normalize_duration(lines[k])
                    break

        # nazwa: najbliższy sensowny tekst przed ceną
        name = ""
        for k in range(p_idx - 1, max(-1, p_idx - 25), -1):
            cand = lines[k]
            if not cand:
                continue
            if cand in SKIP_TEXT:
                continue
            if cand.lower() in {"umów", "usługi", "opinie"}:
                continue
            if COUNT_RE.match(cand):
                continue
            if is_price(cand) or is_duration(cand):
                continue
            # odfiltruj typowe “szumy”
            if re.search(r"(zaoszczędź|promocj|zobacz|mapa|instagram|facebook)", cand, re.I):
                continue
            name = cand
            break

        if not name:
            continue

        rows.append({"name": name, "price": price, "duration": dur})

    # usuń duplikaty (czasem te same wiersze wpadają 2x)
    uniq: List[Dict[str, str]] = []
    seen = set()
    for r in rows:
        key = (r.get("name",""), r.get("price",""), r.get("duration",""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    return uniq

def is_category_heading(h: Tag, cat_names_known: set) -> bool:
    t = clean(h.get_text())
    if t in cat_names_known:
        return True
    # czasem kategoria ma obok “X usług” (np. Usługi męskie)
    # sprawdź najbliższe stringi do kolejnego nagłówka
    probe = []
    for el in h.next_elements:
        if el == h:
            continue
        if is_heading_tag(el):
            break
        if isinstance(el, NavigableString):
            s = clean(str(el))
            if s:
                probe.append(s)
        if len(probe) >= 8:
            break
    return any(COUNT_RE.match(x) for x in probe)

def parse() -> Dict[str, Any]:
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/2.0)"},
        timeout=45,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")

    h_services = find_heading(soup, "Usługi")
    if not h_services:
        raise RuntimeError("Nie znaleziono nagłówka 'Usługi' na stronie Booksy.")

    h_reviews = find_heading(soup, "Opinie")
    if not h_reviews:
        # fallback: jeśli “Opinie” nie istnieje w HTML, utnij po prostu po kawałku
        # (lepiej niż łapać Udogodnienia itd.)
        h_reviews = soup.find(HEADING_TAG_RE)
        if not h_reviews:
            raise RuntimeError("Nie znaleziono nagłówka kończącego (Opinie).")

    # zbierz wszystkie nagłówki między Usługi -> Opinie
    headings: List[Tag] = []
    for h in h_services.find_all_next(HEADING_TAG_RE):
        if h == h_reviews:
            break
        headings.append(h)

    # lista “znanych” nazw kategorii (Booksy może dodać nowe – wtedy zadziała heurystyka z “X usług”)
    known_categories = {
        "Popularne usługi",
        "Usługi męskie",
        "Usługi damskie",
        "Koloryzacja",
        "Zabiegi regenerujące włosy",
        "Prostowanie",
        "Fryzura okolicznościowa",
        "Trwała ondulacja",
    }

    # zidentyfikuj kategorie w tej sekcji
    cat_heads: List[Tag] = []
    for h in headings:
        if is_category_heading(h, known_categories):
            cat_heads.append(h)

    if not cat_heads:
        raise RuntimeError("Nie znaleziono kategorii w sekcji 'Usługi' (Booksy mogło zmienić układ).")

    categories: List[Dict[str, Any]] = []

    for ci, cat_h in enumerate(cat_heads):
        cat_name = clean(cat_h.get_text())
        cat_end = cat_heads[ci + 1] if ci + 1 < len(cat_heads) else h_reviews

        # nagłówki “usług” (karty) wewnątrz kategorii
        svc_heads: List[Tag] = []
        for h in cat_h.find_all_next(HEADING_TAG_RE):
            if h == cat_end:
                break
            # pomiń kolejne kategorie
            if is_category_heading(h, known_categories):
                continue
            t = clean(h.get_text())
            if not t or t.lower() in {"usługi", "opinie"}:
                continue
            svc_heads.append(h)

        items: List[Dict[str, Any]] = []

        for si, svc_h in enumerate(svc_heads):
            svc_title = clean(svc_h.get_text())
            svc_end = svc_heads[si + 1] if si + 1 < len(svc_heads) else cat_end

            block_lines = collect_strings_between(svc_h, svc_end)

            # wyciągnij wiersze z “Umów”
            rows = parse_rows_from_block(block_lines)
            if not rows:
                continue

            # jeśli w obrębie bloku jest 1 pozycja i jej nazwa == tytuł, traktuj jako “pojedynczą usługę”
            def norm(x: str) -> str:
                return re.sub(r"\s+", " ", x).strip().lower()

            if len(rows) == 1 and norm(rows[0]["name"]) == norm(svc_title):
                items.append({
                    "name": svc_title,
                    "price": rows[0]["price"],
                    "duration": rows[0]["duration"],
                })
            else:
                # grupa wariantów pod jednym nagłówkiem
                # jeśli pierwszy wiersz ma tę samą nazwę co nagłówek, zamień na “Cena od”
                if norm(rows[0]["name"]) == norm(svc_title):
                    rows[0]["name"] = "Cena od"
                items.append({
                    "name": svc_title,
                    "variants": rows,
                })

        if items:
            categories.append({"name": cat_name, "items": items})

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories,
    }
    return payload

if __name__ == "__main__":
    import os

    data = parse()
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = 0
    for c in data.get("categories", []):
        for it in c.get("items", []):
            if "variants" in it:
                total += len(it["variants"])
            else:
                total += 1

    print(f"OK -> {OUT_PATH} ({total} pozycji/wariantów)")
