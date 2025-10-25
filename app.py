import asyncio
import aiohttp
from bs4 import BeautifulSoup
import pandas as pd
import io, csv, re
from flask import Flask, render_template, request, make_response
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

def is_potential_tax_code(value):
    if not isinstance(value, str):
        return False
    pattern = re.compile(r'^\d{8,15}(-\d{3})?$')
    return bool(pattern.match(value))

async def fetch_tax_info(session, tax_code):
    url = f"https://masothue.com/Search/?q={tax_code}&type=auto"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        async with session.get(url, headers=headers, timeout=30) as resp:
            if resp.status != 200:
                return {'Tax Code Input': tax_code, 'Status': f'HTTP {resp.status}'}

            html = await resp.text()
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
        return {'Tax Code Input': tax_code, 'Status': f'Scraping Error: {e}'}

async def scrape_all(tax_codes):
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_tax_info(session, code) for code in tax_codes]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            results.append(res)
    return results

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        tax_codes = []

        tax_codes_input = request.form.get('tax_codes', '')
        if tax_codes_input:
            tax_codes.extend([code.strip() for code in tax_codes_input.split(',') if code.strip()])

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

        # convert results to CSV
        si = io.StringIO()
        headers = sorted(set().union(*(r.keys() for r in all_results)))
        if 'Tax Code Input' in headers:
            headers.insert(0, headers.pop(headers.index('Tax Code Input')))
        writer = csv.DictWriter(si, fieldnames=headers)
        writer.writeheader()
        writer.writerows(all_results)
        global csv_output
        csv_output = si.getvalue()

        return render_template('index.html', results=all_results, headers=headers, tax_codes_input=', '.join(tax_codes))
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
    app.run(debug=True)
