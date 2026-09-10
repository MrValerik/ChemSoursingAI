from datetime import datetime, timezone
import re
from decimal import Decimal
from urllib.parse import urlsplit,urlunsplit


def is_valid_cas(value):
    if not re.fullmatch(r"\d{2,7}-\d{2}-\d", value): return False
    ds=value.replace("-", "")
    return sum(i*int(d) for i,d in enumerate(reversed(ds[:-1]),1))%10 == int(ds[-1])


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

_PRICE = re.compile(
    rf"(?P<currency>US\s*\$|USD|EUR|CNY|RMB|\$|€)\s*"
    rf"(?P<low>{_NUMBER})(?:\s*[-–—~]\s*(?P<high>{_NUMBER}))?\s*"
    r"(?:/|per\s+)(?P<unit>kilograms?|kg|metric\s+tons?|mt|tonnes?|tons?|g|l)\b"
    r"(?:\s+(?P<incoterm>FOB|EXW|FCA|CIF|CFR|CIP|CPT|DAP|DPU|DDP|FAS)\b)?",
    re.I,
)

_CAS = re.compile(r"(?<!\d)\d{2,7}-\d{2}-\d(?!\d)")

def product_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc not in ('www.echemi.com', 'www.echemi.com:443')
            or parsed.username or parsed.password
            or not re.fullmatch(r'/produce/[\w-]+\.html', parsed.path)):
        return None
    return urlunsplit(('https', 'www.echemi.com', parsed.path, '', ''))

def parse_offer(block: dict, *, query: str, observed_at: str) -> dict:
    """Keep source and interpretation separate, including failed/ambiguous rows."""
    raw = block['text']
    normalized = ' '.join(raw.split())
    matches = list(_PRICE.finditer(normalized))
    result = {
        'product_url': product_url(block['url']), 'title': block.get('title', ''),
        'seller_url': block.get('seller_url'), 'seller_name': block.get('seller_name'),
        'source_text': raw, 'source_language': block.get('language') or None,
        'observed_at': observed_at, 'query': query,
        'cas_numbers': sorted(set(_CAS.findall(raw))),
        'price_min': None, 'price_max': None, 'currency': None, 'unit': None,
        'incoterm': None, 'price_text': None, 'status': 'price_missing',
        'warnings': ['Published listing; not a confirmed supplier quotation.'],
    }
    if result['product_url'] is None:
        result['status'] = 'invalid_product_url'
        return result
    if is_valid_cas(query) and result['cas_numbers'] != [query]:
        result['warnings'].append('Requested CAS is missing or conflicts within this block.')
        result['status'] = 'identity_unverified'
        return result
    if len(matches) > 1:
        result['status'] = 'ambiguous_price'
        return result
    if not matches:
        if re.search(r'\b(get price|price on request|contact for price)\b', raw, re.I):
            result['status'] = 'price_on_request'
        return result
    match = matches[0]
    low = Decimal(match['low'].replace(',', ''))
    high = Decimal((match['high'] or match['low']).replace(',', ''))
    if low <= 0 or high < low:
        result['status'] = 'invalid_price'
        return result
    currency = match['currency'].upper().replace(' ', '')
    if currency == '$':
        result['warnings'].append('Dollar symbol without an explicit currency code.')
    unit = match['unit'].lower()
    if unit in ('kg', 'kilogram', 'kilograms'):
        unit = 'kg'
    elif unit in ('mt', 'metric ton', 'metric tons', 'tonne', 'tonnes'):
        unit = 'metric_ton'
    elif unit in ('ton', 'tons'):
        result['warnings'].append('Ton definition is unspecified; no unit conversion applied.')
    result.update(price_min=str(low), price_max=str(high),
                  currency={'US$': 'USD', '€': 'EUR', 'RMB': 'CNY'}.get(currency, currency),
                  unit=unit, incoterm=(match['incoterm'] or '').upper() or None,
                  price_text=match[0], status='published_price')
    return result

def is_verification(text: str, title: str = '') -> bool:
    return bool(re.search(
        r'access verification|please slide to verify|verify (?:that )?you are human|'
        r'captcha|access denied|unusual traffic', text, re.I
    )) or title.strip().lower() == 'verification'

_BLOCKS = r"""() => {
  const clean = a => {const u = new URL(a.href); return u.origin + u.pathname;};
  const selector = 'a[href*="/produce/"]';
  const out = new Map();
  for (const a of document.querySelectorAll(selector)) {
    if (!a.getClientRects().length) continue;
    let node = a, best = a;
    for (let i=0; i<7 && node.parentElement; i++) {
      node = node.parentElement;
      if (['BODY','HTML'].includes(node.tagName)) break;
      const urls = new Set([...node.querySelectorAll(selector)].map(clean));
      if (urls.size !== 1) break;
      if ((node.innerText || '').length > 6000) break;
      best = node;
    }
    const seller = best.querySelector('a[href*="/shop-"]');
    const row = {url:clean(a), title:(a.innerText || a.title || '').trim(),
      text:best.innerText || '', seller_url:seller ? seller.href : null,
      seller_name:seller ? seller.innerText.trim() : null,
      language:document.documentElement.lang || null};
    if (!out.has(row.url) || out.get(row.url).text.length < row.text.length)
      out.set(row.url,row);
  }
  return [...out.values()];
}"""

def parse_detail(raw, url):
    lines = [s.strip() for s in raw["text"].splitlines() if s.strip()]
    labels = {"cas": r"CAS(?: No\.?| Number)?", "purity": "Purity|Content",
              "grade": "Grade", "packaging": "Packaging|Package", "minimum_order": r"MOQ|Min\.? Order|Minimum Order Quantity",
              "address": "Address", "contact_person": "Contact Person|Contact Name"}
    fields = {}
    for key, pattern in labels.items():
        fields[key] = []
        for i,line in enumerate(lines):
            match = re.match(r"^(?:" + pattern + r")\s*[:：]\s*(.*)$",line,re.I)
            if not match:
                continue
            value = match[1].strip() or (lines[i+1] if i+1<len(lines) else "")
            if not value or value.endswith((":","：")) or (key=="cas" and not is_valid_cas(value)):
                continue
            if not any(v["value"]==value for v in fields[key]):
                fields[key].append({"value":value,"quote":"\n".join(lines[i:i+2]),"source_url":url})
    contacts = []
    for link in raw["links"]:
        href = link["href"]
        if href.lower().startswith(("mailto:","tel:")):
            value = href.split(":",1)[1].split("?",1)[0]
            owner = "platform_echemi" if value.lower().endswith("@echemi.com") else "unconfirmed"
            contacts.append({"kind":"email" if href.lower().startswith("mailto:") else "telephone",
                             "value":value,"owner":owner,"quote":link["text"],"source_url":url})
    for line in lines:
        for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",line):
            if not any(c["value"]==email for c in contacts):
                contacts.append({"kind":"email","value":email,
                                 "owner":"platform_echemi" if email.lower().endswith("@echemi.com") else "unconfirmed",
                                 "quote":line,"source_url":url})
    return {"title":raw["title"],"source_text":raw["text"][:200000],
            "source_url":url,"source_language":raw["language"],"fields":fields,
            "contacts":contacts,"observed_at":datetime.now(timezone.utc).isoformat(),
            "contact_status":"supplier_contacts_not_confirmed"}
