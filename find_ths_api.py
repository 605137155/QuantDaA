"""
同花顺热门榜单 API 接口探测脚本
使用 Playwright 访问页面并捕获所有网络请求
"""

import asyncio
from playwright.async_api import async_playwright

async def find_ths_api():
    """使用 Playwright 访问同花顺热门榜单页面，捕获所有网络请求"""

    print("[*] 开始探测同花顺热门榜单 API 接口...")
    print("=" * 60)

    captured_requests = []

    async with async_playwright() as p:
        # 启动浏览器（非无头模式，可以观察）
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 监听所有网络请求
        async def on_request(request):
            url = request.url
            method = request.method
            resource_type = request.resource_type

            # 只关注 XHR/Fetch 请求
            if resource_type in ['xhr', 'fetch']:
                captured_requests.append({
                    'url': url,
                    'method': method,
                    'headers': dict(request.headers)
                })
                print(f"[Request] [{method}] {url}")

        page.on('request', on_request)

        try:
            # 访问同花顺热门榜单页面
            target_url = "https://eq.10jqka.com.cn/frontend/thsTopRank/index.html"
            print(f"\n[Visit] 正在访问: {target_url}\n")

            await page.goto(target_url, timeout=30000)

            # 等待页面加载和数据请求
            print("\n[Wait] 等待页面加载和数据请求 (5秒)...")
            await page.wait_for_timeout(5000)

            # 尝试滚动页面触发更多请求
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

        except Exception as e:
            print(f"[Error] 访问出错: {e}")

        finally:
            await browser.close()

    # 输出结果
    print("\n" + "=" * 60)
    print(f"[Result] 共捕获到 {len(captured_requests)} 个 XHR/Fetch 请求")
    print("=" * 60)

    if captured_requests:
        print("\n[API] 捕获到的 API 请求:")
        for i, req in enumerate(captured_requests, 1):
            print(f"\n{i}. [{req['method']}] {req['url']}")

            # 检查是否可能是数据接口
            url = req['url'].lower()
            if any(keyword in url for keyword in ['rank', 'hot', 'list', 'data', 'api', 'stock']):
                print(f"   [!] 可能是数据接口!")
    else:
        print("\n[Warning] 未捕获到 XHR/Fetch 请求")
        print("可能原因:")
        print("1. 页面使用了其他方式加载数据")
        print("2. 需要登录或特定条件")
        print("3. 页面加载超时")

    return captured_requests

if __name__ == "__main__":
    requests = asyncio.run(find_ths_api())