# app.py
import asyncio
import aiohttp
import random
import time
import re
import io
import csv
from bs4 import BeautifulSoup
import pandas as pd
from flask import Flask, render_template, request, make_response

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

# ---------------------------
# Cấu hình (tùy chỉnh ở đây)
# ---------------------------
CONCURRENCY = 6               # số request đồng thời; để 3-6 nếu muốn an toàn
MIN_DELAY = 0.8               # delay nhỏ nhất trước mỗi request (giây)
MAX_DELAY = 2.5               # delay lớn nhất trước mỗi request (giây)
MAX_RETRIES = 1               # số lần thử lại khi lỗi tạm thời

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; rv:117.0) Gecko/20100101 Firefox/117.0",
]

PROXIES = [
    None,  # None nghĩa là không dùng proxy; thêm proxy strings nếu có
    # "http://user:pass@1.2.3.4:8080",
]

# ---------------------------
# Utils
# ---------------------------
def is_potential_tax_code(value):
    if not isinstance(value, str):
        return False
    pattern = re.compile(r'^\d{8,15}(-\d{3})?$')
    return bool(pattern.match(value.strip()))

def choose_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://masothue.com/",
        "Connection": "keep-alive"
    }

# ---------------------------
# Fetch + parse
# ---------------------------
async def fetch_once(session, url, headers, proxy=None):
    try:
        async with session.get(url, headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=25)) as resp:
            status = resp.status
            text = await resp.text()
            return status, text
    except asyncio.TimeoutError:
        return None, "Timeout"
    except aiohttp.ClientError as e:
        return None, f"ClientError: {e}"
    except Exception as e:
        return None, f"Error: {e}"

async def fetch_with_retry(session, url, headers, proxy=None, retries=MAX_RETRIES):
    attempt = 0
    backoff = 1.0
    while True:
        status, text = await fetch_once(session, url, headers, proxy)
        if status is not None and isinstance(status, int) and status == 200:
            return status, text
        # Nếu có nội dung trả về (ví dụ 403), trả luôn để xử lý
        if status is not None and isinstance(status, int) and status != 200:
            return status, text
        # Nếu thất bại (None) và còn retry => chờ và retry
        if attempt < retries:
            await asyncio.sleep(backoff + random.uniform(0, 0.5))
            backoff *= 2
            attempt += 1
            continue
        # hết retry => trả lỗi
        return status, text

async def fetch_tax_info(session, sem, tax_code):
    url = f"https://masothue.com/Search/?q={tax_code}&type=auto"
    # random delay trước khi request để tránh burst
    await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    headers = choose_headers()
    proxy = random.choice(PROXIES) if PROXIES else None

    async with sem:
        status, text = await fetch_with_retry(session, url, headers, proxy)
        if status is None:
            return {'Tax Code Input': tax_code, 'Status': f'Request failed: {text}'}
        if isinstance(status, int) and status != 200:
            # 403/404/500 -> trả trạng thái cho UI
            if status == 403:
                # kiểm tra nội dung captcha nếu có
                low = (text or "").lower()
                if "captcha" in low or "access denied" in low or "you are being" in low:
                    return {'Tax Code Input': tax_code, 'Status': 'Blocked (captcha/403)'}
            return {'Tax Code Input': tax_code, 'Status': f'HTTP {status}'}

        # parse HTML
        low = (text or "").lower()
        if "captcha" in low or "access denied" in low or "you are being" in low:
            return {'Tax Code Input': tax_code, 'Status': 'Blocked (captcha/403)'}
        soup = BeautifulSoup(text, 'html.parser')
        table = soup.find('table', class_='table-taxinfo')
        if not table:
            return {'Tax Code Input': tax_code, 'Status': 'Not found'}
        company_info = {'Tax Code Input': tax_code}
        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).replace(':', '').strip()
                val = cells[1].get_text(strip=True)
                company_info[key] = val
        return company_info

async def scrape_all_async(tax_codes):
    results = []
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit_per_host=CONCURRENCY, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [fetch_tax_info(session, sem, code) for code in tax_codes]
        for coro in asyncio.as_completed(tasks):
            try:
                res = await coro
            except Exception as e:
                res = {'Tax Code Input': 'UNKNOWN', 'Status': f'Unhandled error: {e}'}
            results.append(res)
    return results

# ---------------------------
# Flask routes
# ---------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        tax_codes = []
        tax_codes_input = request.form.get('tax_codes', '')
        if tax_codes_input:
            tax_codes.extend([c.strip() for c in tax_codes_input.split(',') if c.strip()])

        file = request.files.get('file')
        if file and file.filename:
            try:
                if file.filename.lower().endswith('.csv'):
                    df = pd.read_csv(file, dtype=str, header=None)
                else:
                    df = pd.read_excel(file, dtype=str, header=None)
                for col in df.columns:
                    for item in df[col]:
                        if pd.notna(item) and is_potential_tax_code(str(item)):
                            tax_codes.append(str(item).strip())
            except Exception as e:
                return render_template('index.html', error=f"Error processing file: {e}")

        tax_codes = sorted(set(tax_codes))
        if not tax_codes:
            return render_template('index.html', error="No valid tax codes found.")

        start = time.time()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        all_results = loop.run_until_complete(scrape_all_async(tax_codes))
        elapsed = time.time() - start
        print(f"Scraped {len(all_results)} codes in {elapsed:.2f}s")

        si = io.StringIO()
        headers = sorted(set().union(*(r.keys() for r in all_results)))
        if 'Tax Code Input' in headers:
            headers.insert(0, headers.pop(headers.index('Tax Code Input')))
        writer = csv.DictWriter(si, fieldnames=headers)
        writer.writeheader()
        writer.writerows(all_results)
        global csv_output
        csv_output = si.getvalue()

        return render_template('index.html', results=all_results, headers=headers, tax_codes_input=', '.join(tax_codes), elapsed=elapsed)
    return render_template('index.html')

@app.route('/download_csv')
def download_csv():
    if 'csv_output' in globals() and csv_output:
        output = make_response(csv_output)
        output.headers["Content-Disposition"] = "attachment; filename=tax_info_results.csv"
        output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        return output
    return "No data to download.", 404

if __name__ == '__main__':
    csv_output = None
    app.run(host="0.0.0.0", port=8080, debug=False)
