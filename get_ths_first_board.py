import asyncio
import os
import sys
import re
import urllib.parse
import csv
import argparse
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

EXPORTS_DIR = "exports"

def parse_target_date(date_str):
    # Clean the input date string
    cleaned = re.sub(r'[-/._\s]', '', date_str)
    if len(cleaned) == 8:
        year = int(cleaned[:4])
        month = int(cleaned[4:6])
        day = int(cleaned[6:])
        return datetime(year, month, day)
    else:
        raise ValueError(f"无法解析的日期格式: {date_str}。请使用 YYYY-MM-DD 或 YYYYMMDD 格式。")

def get_previous_trading_day(dt):
    # Calculate previous trading day (skipping weekends)
    prev = dt - timedelta(days=1)
    while prev.weekday() >= 5:  # 5 is Saturday, 6 is Sunday
        prev -= timedelta(days=1)
    return prev

async def extract_table_page(page):
    """Extract table rows and headers from the current page of iwencai search results"""
    return await page.evaluate("""() => {
        const tables = document.querySelectorAll('table');
        if (tables.length === 0) return [null, []];
        
        // Find the table that contains stock codes (typically has 6-digit codes)
        // Usually, there are two aligned tables (left frozen and right scrollable)
        // We will extract code and name from the table that contains them
        let codeTable = null;
        let dataTable = null;
        
        for (let t of tables) {
            const rows = t.querySelectorAll('tbody tr, tr');
            if (rows.length > 0) {
                const cells = rows[0].querySelectorAll('td, th');
                // Check if any cell has a 6-digit stock code
                const hasCode = Array.from(t.querySelectorAll('td')).some(td => /^\\d{6}$/.test(td.innerText.trim()));
                if (hasCode) {
                    if (cells.length > 5) {
                        dataTable = t;
                    } else {
                        codeTable = t;
                    }
                }
            }
        }
        
        // Fallbacks if tables aren't found
        if (!codeTable && !dataTable) {
            dataTable = tables[0];
        } else if (!dataTable) {
            dataTable = codeTable;
        } else if (!codeTable) {
            codeTable = dataTable;
        }
        
        // Extract headers from dataTable
        const headerCells = dataTable.querySelectorAll('thead th, th');
        const headersList = Array.from(headerCells).map(cell => cell.innerText.trim().replace(/\\n/g, ' '));
        
        // Extract row data
        const rows = dataTable.querySelectorAll('tbody tr, tr');
        const rowsList = [];
        
        for (let row of rows) {
            const cells = row.querySelectorAll('td');
            if (cells.length > 0) {
                const cellValues = Array.from(cells).map(c => c.innerText.trim());
                // Only include rows that have at least one cell matching a 6-digit stock code
                const hasCode = cellValues.some(val => /^\\d{6}$/.test(val));
                if (hasCode) {
                    rowsList.push(cellValues);
                }
            }
        }
        
        return [headersList, rowsList];
    }""")

async def run_crawler(query_str, date_filename):
    print("[*] Launching headless browser with stealth settings...")
    
    async with async_playwright() as p:
        # Launch browser headlessly
        browser = await p.chromium.launch(headless=True)
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        
        # Inject basic stealth script to bypass bot detection (important for iwencai)
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)
        
        encoded_query = urllib.parse.quote(query_str)
        target_url = f"https://www.iwencai.com/unifiedwap/result?w={encoded_query}"
        
        print(f"[*] Navigating to: {target_url}")
        try:
            await page.goto(target_url, wait_until="load", timeout=45000)
        except Exception as e:
            print(f"[!] Warning: Page navigation timed out or encountered error: {e}")
            
        await page.wait_for_timeout(5000)
        
        # Check if no results were found
        body_text = await page.evaluate("() => document.body.innerText")
        if "抱歉，未选出" in body_text:
            print(f"[!] 同花顺未选出与该条件相关的股票。可能是该日期为非交易日，或者该日期还没有首板股票数据。")
            await browser.close()
            return None
            
        print("[*] Extracting Page 1...")
        headers, page1_rows = await extract_table_page(page)
        
        if not page1_rows:
            print("[!] 无法提取表格数据。")
            await browser.close()
            return None
            
        print(f"[+] Page 1 extracted {len(page1_rows)} stocks.")
        
        all_rows = list(page1_rows)
        current_page = 1
        
        # Pagination loop
        while True:
            next_page = current_page + 1
            # Check if there is a button for the next page
            has_next_page = await page.evaluate("""(next_page) => {
                const elements = document.querySelectorAll('.pager a, .pager li, .pager .page-item');
                for (let el of elements) {
                    if (el.innerText.trim() === String(next_page)) {
                        return true;
                    }
                }
                return false;
            }""", next_page)
            
            if not has_next_page:
                break
                
            print(f"[*] Clicking Page {next_page} button...")
            await page.evaluate("""(next_page) => {
                const elements = document.querySelectorAll('.pager a, .pager li');
                for (let el of elements) {
                    if (el.innerText.trim() === String(next_page)) {
                        el.click();
                    }
                }
            }""", next_page)
            
            # Wait for the next page to load (wait for the index of the first row to update)
            expected_index = str((next_page - 1) * 50 + 1)
            print(f"[*] Waiting for row 1 index to become '{expected_index}'...")
            
            try:
                js_expression = f"""() => {{
                    const tables = document.querySelectorAll('table');
                    for (let t of tables) {{
                        const firstRow = t.querySelector('tbody tr, tr');
                        if (firstRow) {{
                            const cells = firstRow.querySelectorAll('td');
                            if (cells.length > 0 && cells[0].innerText.trim() === '{expected_index}') {{
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }}"""
                await page.wait_for_function(js_expression, timeout=10000)
                print(f"[+] Page {next_page} loaded successfully!")
            except Exception as e:
                print(f"[!] Warning waiting for page {next_page} load: {e}")
                break
                
            _, page_rows = await extract_table_page(page)
            if page_rows:
                print(f"[+] Page {next_page} extracted {len(page_rows)} stocks.")
                all_rows.extend(page_rows)
            else:
                break
                
            current_page = next_page
            
        print(f"[+] Total extracted {len(all_rows)} stock rows.")
        
        # Save results to CSV
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        csv_file = os.path.join(EXPORTS_DIR, f"{date_filename}_first_board.csv")
        
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if headers:
                writer.writerow(headers)
            writer.writerows(all_rows)
            
        print(f"[+] Data successfully saved to {csv_file}")
        
        await browser.close()
        return headers, all_rows

def main():
    parser = argparse.ArgumentParser(description="爬取同花顺任意指定日期的首板个股 (A股)")
    parser.add_argument("date", nargs="?", help="目标日期，格式为 YYYYMMDD 或 YYYY-MM-DD (例如 20260227)")
    parser.add_argument("--prev", action="store_true", help="如果设置该选项，则爬取指定日期的前一个交易日的首板个股")
    
    args = parser.parse_args()
    
    # Check if a date is specified
    if args.date:
        try:
            target_dt = parse_target_date(args.date)
        except Exception as e:
            print(f"[!] {e}")
            sys.exit(1)
    else:
        # Default target date is 2026-03-02, and we want its previous trading day (2026-02-27)
        # This matches the user's specific request "20260302的前一日的首板个股"
        target_dt = datetime(2026, 3, 2)
        args.prev = True
        print("[*] 未指定日期，默认计算 2026-03-02 的前一个交易日...")
        
    if args.prev:
        scrape_dt = get_previous_trading_day(target_dt)
        print(f"[*] 计算得到前一交易日: {scrape_dt.strftime('%Y-%m-%d')}")
    else:
        scrape_dt = target_dt
        
    date_formatted = scrape_dt.strftime("%Y年%m月%d日")
    date_filename = scrape_dt.strftime("%Y%m%d")
    
    query_str = f"{date_formatted}首板"
    print(f"[*] 爬取日期: {scrape_dt.strftime('%Y-%m-%d')} ({date_formatted})")
    print(f"[*] 同花顺查询词: {query_str}")
    
    # Run the async crawler
    result = asyncio.run(run_crawler(query_str, date_filename))
    
    if result:
        headers, rows = result
        print("\n" + "="*80)
        print(f" {scrape_dt.strftime('%Y-%m-%d')} 首板个股提取结果 (共 {len(rows)} 只):")
        print("="*80)
        
        # Locate indices for stock code and name
        code_idx = 2
        name_idx = 3
        for idx, h in enumerate(headers or []):
            if "代码" in h:
                code_idx = idx
            elif "简称" in h or "名称" in h:
                name_idx = idx
                
        # Format table header
        print(f"{'序号':<5} | {'代码':<8} | {'简称':<12} | {'行数据预览'}")
        print("-"*80)
        
        for idx, r in enumerate(rows, 1):
            code = r[code_idx] if len(r) > code_idx else "N/A"
            name = r[name_idx] if len(r) > name_idx else "N/A"
            preview = " ".join([r[i] for i in range(len(r)) if i not in (0, 1, code_idx, name_idx) and r[i]])[:60]
            print(f"{idx:<5} | {code:<8} | {name:<12} | {preview}")
        print("="*80 + "\n")
    else:
        print("[!] 爬取失败或未找到数据。")
        sys.exit(1)

if __name__ == "__main__":
    main()
