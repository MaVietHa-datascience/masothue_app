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
CONCURRENCY = 5               # số request đồng thời (3-5 là ổn định)
REQUESTS_PER_MINUTE = 20      # chỉ để tham khảo (không áp dụng cứng)
MAX_RETRIES = 2               # retry cho lỗi tạm thời
MIN_DELAY = 0.4               # delay ngắn trước mỗi request (giảm bot detection)
MAX_DELAY = 1.0

# User-agents rotate
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; rv:117.0) Gecko/20100101 Firefox/117.0",
]

# Optional: nếu bạn có proxies chất lượng đặt ở đây (format: "http://user:pass@host:port" or "http://host:port")
# Để None nếu không dùng
PROXIES = [
    None,
    # "http://username:password@1.2.3.4:8080",
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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://masothue.com/",
        "Connection": "keep-alive"
    }

# ---------------------------
# Fetch + parse
# ---------------------------
async def fetch_with_retry(session, url, headers, proxy=None, max_retries=MAX_RETRIES):
    backoff = 1.0
    for attempt in range(max_retries + 1):
        try:
            async with session.get(url, headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                status = resp.status
                text = await resp.text()
                return status, text
        except asyncio.TimeoutError:
            err = "Timeout"
        except aiohttp.ClientError as e:
            err = f"ClientError: {e}"
        except Exception as e:
            err = f"OtherError: {e}"

        # retry logic
        if attempt < max_retries:
            await asyncio.sleep(backoff + random.uniform(0, 0.5))
            backoff *= 2
        else:
            return None, f"Request failed after retries: {err}"

async def fetch_tax_info(session, sem, tax_code):
    url = f"https://masothue.com/Search/?q={tax_code}&type=auto"
    # small randomized delay to avoid bursts
    await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    headers = choose_headers()
    proxy = random.choice(PROXIES) if PROXIES else None

    async with sem:  # limit concurrency
        status, text = await fetch_with_retry(session, url, headers=headers, proxy=proxy)
        if status is None:
            return {'Tax Code Input': tax_code, 'Status': text}
        if isinstance(status, int) and status != 200:
            return {'Tax Code Input': tax_code, 'Status': f'HTTP {status}'}
        # quick block/captcha detection by inspecting text
        low = text.lower()
        if "captcha" in low or "access denied" in low or "you are being" in low:
            return {'Tax Code Input': tax_code, 'Status': 'Blocked (captcha/403)'}
        # parse
        soup = BeautifulSoup(text, 'html.parser')
        table = soup.find('table', class_='table-taxinfo')
        if not table:
            # sometimes page shows "Không tìm thấy" or similar; return Not found
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
            res = await coro
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

        # Run async scraping
        start = time.time()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        all_results = loop.run_until_complete(scrape_all_async(tax_codes))
        elapsed = time.time() - start
        print(f"Scraped {len(all_results)} codes in {elapsed:.2f}s")

        # Build CSV
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
