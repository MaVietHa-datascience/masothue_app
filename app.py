import requests
from flask import Flask, render_template, request, make_response
from bs4 import BeautifulSoup
import csv
import io

app = Flask(__name__)

def scrape_tax_code(tax_code):
    """
    Scrapes masothue.com for a given tax code and returns the company information.
    """
    url = 'https://masothue.com/Search/'
    params = {'q': tax_code, 'type': 'auto'}
    headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        table = soup.find('table', class_='table-taxinfo')
        
        if table:
            company_info = {'Tax Code Input': tax_code}
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True).replace(':', '').strip()
                    value = cells[1].get_text(strip=True)
                    company_info[key] = value
            return company_info
        else:
            return {'Tax Code Input': tax_code, 'Status': 'Information not found'}

    except requests.exceptions.RequestException as e:
        return {'Tax Code Input': tax_code, 'Status': f'Request Error: {e}'}

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        tax_codes_input = request.form['tax_codes']
        tax_codes = [code.strip() for code in tax_codes_input.split(',') if code.strip()]
        
        if not tax_codes:
            return render_template('index.html', error="Please enter at least one tax code.")

        all_results = []
        for code in tax_codes:
            info = scrape_tax_code(code)
            if info:
                all_results.append(info)
        
        # --- Prepare data for CSV download ---
        if all_results:
            # Use an in-memory string buffer to build the CSV
            si = io.StringIO()
            all_headers = set()
            for result in all_results:
                all_headers.update(result.keys())
            
            ordered_headers = sorted(list(all_headers))
            if 'Tax Code Input' in ordered_headers:
                ordered_headers.insert(0, ordered_headers.pop(ordered_headers.index('Tax Code Input')))

            writer = csv.DictWriter(si, fieldnames=ordered_headers)
            writer.writeheader()
            writer.writerows(all_results)
            
            # Store CSV content in a global variable or session for download
            # Note: Using a global is simple for this example, but a session would be better for multi-user scenarios
            global csv_output
            csv_output = si.getvalue()

            return render_template('index.html', results=all_results, headers=ordered_headers, tax_codes_input=tax_codes_input)

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
