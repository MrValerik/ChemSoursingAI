"""Check visible verification UI, rather than hidden challenge templates."""
async def needs_verification(page):
    if (await page.title()).strip().lower() in {"verification", "access denied"}:
        return True
    if await page.locator("#aliyunCaptcha-sliding-slider").is_visible():
        return True
    return await page.get_by_text("Access Verification", exact=True).first.is_visible()
