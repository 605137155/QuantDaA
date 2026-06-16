import asyncio
import urllib.parse
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1200} # Increased height to see more
        )
        page = await context.new_page()
        
        # Stealth
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)
        
        query = "2026年2月27日首板"
        encoded_query = urllib.parse.quote(query)
        target_url = f"https://www.iwencai.com/unifiedwap/result?w={encoded_query}"
        
        print(f"[*] Navigating to: {target_url}")
        await page.goto(target_url, wait_until="load", timeout=30000)
        await page.wait_for_timeout(5000)
        
        # Scroll page to the very bottom of the body
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
        
        # Save screenshot at the bottom
        await page.screenshot(path="screenshot_bottom.png")
        print("[*] Saved screenshot_bottom.png")
        
        # Let's inspect potential pagination elements
        pag_info = await page.evaluate("""() => {
            const elements = document.querySelectorAll('*');
            const pag = [];
            elements.forEach(el => {
                const text = el.innerText ? el.innerText.trim() : '';
                // Check if class or text matches pagination patterns
                const isPaginationClass = el.className && typeof el.className === 'string' && el.className.toLowerCase().includes('page');
                const isPaginationText = text === '下一页' || text === '2' || (text.includes('下一页') && text.length < 50);
                
                if (isPaginationClass || isPaginationText) {
                    pag.push({
                        tagName: el.tagName,
                        className: el.className,
                        text: text.slice(0, 100),
                        id: el.id
                    });
                }
            });
            return pag;
        }""")
        
        print(f"[*] Found {len(pag_info)} potential pagination elements:")
        for idx, p_el in enumerate(pag_info[:15]):
            print(f"  {idx}: <{p_el['tagName']}> class='{p_el['className']}' id='{p_el['id']}' text='{p_el['text']}'")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
