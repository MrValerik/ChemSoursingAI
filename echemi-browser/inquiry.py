"""Fill a single visible product inquiry form. Never guess unknown required data."""
import asyncio
import re
from parsing import product_url
from page_state import needs_verification

# Exact semantic aliases, not LLM instructions from the page.
ALIASES = {
    "email": {"email", "emailaddress", "youremail", "emailforreply"},
    "company_name": {"company", "companyname", "yourcompany"},
    "contact_name": {"name", "fullname", "contactname", "contactperson", "yourname"},
    "phone": {"phone", "telephone", "tel", "mobile", "phonenumber", "mobilenumber"},
    "country": {"country", "countryregion"},
    "city": {"city", "town"},
    "region": {"region", "state", "province"},
    "address": {"address", "streetaddress"},
    "postal_code": {"postalcode", "zipcode", "zip"},
    "job_title": {"jobtitle", "position"},
    "website": {"website", "companywebsite"},
    "whatsapp": {"whatsapp", "whatsappnumber"},
    "wechat": {"wechat", "wechatid"},
    "message": {"message", "content", "inquiry", "inquirycontent", "yourmessage", "requirements", "details"},
}


def field_key(field):
    keys = set()
    for value in field.get("hints", []):
        normalized = re.sub(r"[^a-z]", "", value.lower())
        normalized = re.sub(r"^(?:pleaseenter|enter|your)", "", normalized)
        for key, aliases in ALIASES.items():
            if normalized in aliases:
                keys.add(key)
    return next(iter(keys)) if len(keys) == 1 else None


def plan_fields(fields, sender, message):
    plan = []
    for field in fields:
        key = field_key(field)
        if field["type"] in {"checkbox", "radio", "file", "password"}:
            if field["required"] or field["type"] == "password":
                return None, "unsupported_fields"
            continue
        if not key:
            if field["required"]:
                return None, "unsupported_fields"
            continue
        value = message if key == "message" else sender.get(key, "")
        if not value:
            if field["required"]:
                return None, "missing_fields"
            continue
        if field.get("max_length", -1) > 0 and len(value) > field["max_length"]:
            return None, "unsupported_fields"  # No silent truncation of approved text.
        plan.append((field["index"], key, value, field["type"]))
    if not {"email", "message"}.issubset({item[1] for item in plan}):
        return None, "form_unavailable"
    return plan, None


# Scope to a unique visible form containing a message box and an inquiry submit
# control. Ignore search, subscription and related-product forms.
FORM = r"""() => {
 const visible=e=>!!e.getClientRects().length;
 const submit=e=>/^(send|submit|send inquiry|send message|submit inquiry|inquire now)$/i.test((e.innerText||e.value||'').trim());
 const candidates=new Set();
 for (const t of document.querySelectorAll('textarea')) {
   if(!visible(t)) continue;
   let parent=t.parentElement;
   for(let depth=0;parent && depth<6;depth++,parent=parent.parentElement) {
     const buttons=[...parent.querySelectorAll('button,a,input[type=submit]')].filter(e=>visible(e)&&submit(e));
     const emails=[...parent.querySelectorAll('input')].filter(e=>visible(e)&&/email/i.test([e.type,e.name,e.id,e.placeholder].join(' ')));
     if(buttons.length===1&&emails.length===1) {candidates.add(parent);break;}
   }
 }
 if(candidates.size!==1)return null;
 const root=[...candidates][0];
 root.setAttribute('data-chemsource-inquiry','current');
 const buttons=[...root.querySelectorAll('button,a,input[type=submit]')].filter(e=>visible(e)&&submit(e));
 buttons[0].setAttribute('data-chemsource-submit','current');
 const fields=[...root.querySelectorAll('input,textarea,select')].filter(e=>visible(e)&&e.type!=='submit'&&e.type!=='button'&&!e.disabled);
 return fields.map((e,index)=>{
   e.setAttribute('data-chemsource-field',String(index));
   return {index,type:e.tagName==='SELECT'?'select':(e.type||'text'),
     required:e.required||e.getAttribute('aria-required')==='true',
     max_length:e.maxLength||-1,
     hints:[e.name,e.id,e.placeholder,e.getAttribute('aria-label'),...[...(e.labels||[])].map(l=>l.innerText)].filter(Boolean)};
 });
}"""

SUCCESS = re.compile(r"^(?:your )?(?:inquiry|message) (?:has been )?(?:sent|submitted) successfully[.!]?$", re.I)


async def submit(page, url, sender, message, *, seller_name=None, verify=None):
    attempted = False
    stage = "navigation"
    async def check(stage):
        if verify is not None:
            return await verify(stage)
        return "verification_required" if await needs_verification(page) else None
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if reason := await check(stage):
            return {"status": "blocked", "reason": reason}
        if product_url(page.url) != url:
            return {"status": "blocked", "reason": "recipient_changed"}
        if seller_name:
            seller = page.get_by_role("link", name=seller_name, exact=True)
            matched = False
            for link in await seller.all():
                href = await link.get_attribute("href") or ""
                if await link.is_visible() and re.fullmatch(r"(?:https://www\.echemi\.com)?/shop-[\w-]+/index\.html", href):
                    matched = True
            if not matched:
                return {"status": "blocked", "reason": "recipient_changed"}
        stage = "form_open"
        fields = await page.evaluate(FORM)
        if fields is None:
            # This opens the product's own price inquiry, never related rows.
            opener = page.get_by_text(re.compile(r"^Get (?:Latest |Best )?Price\s*[\ue000-\uf8ff]?$", re.I))
            if await opener.count() != 1:
                return {"status": "blocked", "reason": "form_unavailable"}
            await opener.click(timeout=10000)
            await asyncio.sleep(1)
            if reason := await check(stage):
                return {"status": "blocked", "reason": reason}
            fields = await page.evaluate(FORM)
        if fields is None:
            return {"status": "blocked", "reason": "form_unavailable"}
        plan, reason = plan_fields(fields, sender, message)
        if reason:
            return {"status": "blocked", "reason": reason}
        stage = "form_fill"
        for index, key, value, kind in plan:
            field = page.locator(f'[data-chemsource-field="{index}"]')
            if kind == "select":
                options = await field.locator("option").evaluate_all(
                    "(items)=>items.map(e=>({value:e.value,label:e.textContent.trim()}))")
                labels = {value.upper()}
                if key == "country":
                    label = await page.evaluate("(code)=>new Intl.DisplayNames(['en'],{type:'region'}).of(code)", value)
                    labels.add(label.upper())
                    if value == "RU":
                        labels.add("RUSSIAN FEDERATION")
                matches = [o for o in options if o["value"].upper() == value.upper() or o["label"].upper() in labels]
                if len(matches) != 1:
                    return {"status": "blocked", "reason": "unsupported_fields"}
                await field.select_option(matches[0]["value"])
            else:
                await field.fill(value)
        if product_url(page.url) != url:
            return {"status": "blocked", "reason": "recipient_changed"}
        stage = "pre_submit"
        root = page.locator('[data-chemsource-inquiry="current"]')
        if reason := await check(stage):
            return {"status": "blocked", "reason": reason}
        valid = await root.evaluate("(r)=>[...r.querySelectorAll('input,select,textarea')].every(e=>!e.willValidate||e.checkValidity())")
        if not valid:
            return {"status": "blocked", "reason": "missing_fields"}
        # Only a newly appearing explicit confirmation can mean sent.
        alerts = page.get_by_text(SUCCESS)
        before = {await alert.inner_text() for alert in await alerts.all() if await alert.is_visible()}
        attempted = True
        await page.locator('[data-chemsource-submit="current"]').click(timeout=10000)
        for _ in range(15):
            for alert in await alerts.all():
                text = (await alert.inner_text()).strip()
                if await alert.is_visible() and text not in before and SUCCESS.fullmatch(text):
                    return {"status": "sent"}
            await asyncio.sleep(1)
        return {"status": "unknown"}
    except Exception:
        return {"status": "unknown"} if attempted else {"status": "blocked", "reason": {
            "navigation": "page_load_failed", "form_open": "form_open_failed",
            "form_fill": "form_fill_failed", "pre_submit": "form_validation_failed",
        }[stage]}
