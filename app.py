import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import pandas as pd
import io, csv, re, random, time
from flask import Flask, render_template, request, make_response

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

# ---------------------------
# 🧩 DANH SÁCH USER-AGENTS
# ---------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    "Mozilla/5.0 (Linux; Android 13; SM-G998B)"
]

# ---------------------------
# 🔍 KIỂM TRA MÃ SỐ THUẾ
# ---------------------------
def is_potential_tax_code(value):
    if not isinstance(value, str):
        return False
    pattern = re.compile(r'^\d{8,15}(-\d{3})?$')
    return bool(pattern.match(value))

# ---------------------------
# 🚀 SCRAPE MỘT MÃ SỐ THUẾ
# ---------------------------
async def scrape_one(playwright, tax_code):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
    page = await context.new_page()

    try:
        url = f"https://masothue.com/Search/?q={tax_code}&type=auto"
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(random.randint(1200, 2500))

        html = await page.content()
        ##if "captcha" in html.lower():
          ##  return {'Tax Code Input': tax_code, 'Status': 'Blocked (captcha/403)'}

        soup = BeautifulSoup(html, 'html.parser')
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

    except Exception as e:
        return {'Tax Code Input': tax_code, 'Status': f'Error: {e}'}

    finally:
        await browser.close()

# ---------------------------
# 🕸️ SCRAPE TOÀN BỘ DANH SÁCH
# ---------------------------
async def scrape_all(tax_codes):
    results = []
    async with async_playwright() as p:
        sem = asyncio.Semaphore(3)  # chạy song song 3 trình duyệt
        async def bounded(code):
            async with sem:
                res = await scrape_one(p, code)
                await asyncio.sleep(random.uniform(1.5, 3))
                return res

        tasks = [bounded(code) for code in tax_codes]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
    return results

# ---------------------------
# 🌐 ROUTE CHÍNH
# ---------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        start_time = time.time()
        tax_codes = []

        # lấy input text
        tax_codes_input = request.form.get('tax_codes', '')
        if tax_codes_input:
            tax_codes.extend([code.strip() for code in tax_codes_input.split(',') if code.strip()])

        # lấy file
        file = request.files.get('file')
        if file and file.filename:
            try:
                if file.filename.endswith('.csv'):
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

        print(f"Scraping {len(tax_codes)} codes...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        all_results = loop.run_until_complete(scrape_all(tax_codes))

        # tạo CSV
        si = io.StringIO()
        headers = sorted(set().union(*(r.keys() for r in all_results)))
        if 'Tax Code Input' in headers:
            headers.insert(0, headers.pop(headers.index('Tax Code Input')))
        writer = csv.DictWriter(si, fieldnames=headers)
        writer.writeheader()
        writer.writerows(all_results)
        global csv_output
        csv_output = si.getvalue()

        elapsed = time.time() - start_time
        return render_template('index.html', results=all_results, headers=headers,
                               tax_codes_input=', '.join(tax_codes), elapsed=elapsed)

    return render_template('index.html')

# ---------------------------
# 📥 TẢI FILE CSV
# ---------------------------
@app.route('/download_csv')
def download_csv():
    if 'csv_output' in globals() and csv_output:
        output = make_response(csv_output)
        output.headers["Content-Disposition"] = "attachment; filename=tax_info_results.csv"
        output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
        return output
    return "No data to download.", 404

# ---------------------------
# 🚀 MAIN
# ---------------------------
if __name__ == '__main__':
    csv_output = None
    app.run(debug=True)
