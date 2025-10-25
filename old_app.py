import requests
from flask import Flask, render_template, request, make_response, Response, stream_with_context
from bs4 import BeautifulSoup
import csv
import io
import pandas as pd
import os
import re
import time

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

def scrape_tax_code(tax_code):
    """
    Scrapes masothue.com for a given tax code and returns the company information.
    """
    url = 'https://masothue.com/Search/'
    params = {'q': tax_code, 'type': 'auto'}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        table = soup.find('table', class_='table-taxinfo')
        
        if table:
            company_info = {'Code Input': tax_code}
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True).replace(':', '').strip()
                    value = cells[1].get_text(strip=True)
                    company_info[key] = value
            return company_info
        else:
            return {'Code Input': tax_code, 'Status': 'Information not found'}

    except requests.exceptions.RequestException as e:
        return {'Code Input': tax_code, 'Status': f'Request Error: {e}'}

def is_potential_tax_code(value):
    """
    Checks if a value is likely a tax code.
    Allows for 10 digits, or 10 digits followed by a hyphen and 3 digits.
    """
    if not isinstance(value, str):
        return False
    pattern = re.compile(r'^\d{5,15}(-\d{1,5})?$')
    return bool(pattern.match(value))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        tax_codes = []
        
        tax_codes_input = request.form['tax_codes']
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
                        if pd.notna(item) and is_potential_tax_code(str(item).strip()):
                            tax_codes.append(str(item).strip())
            except Exception as e:
                return render_template('index.html', error=f"Error processing file: {e}")

        tax_codes = sorted(list(set(tax_codes)))

        if not tax_codes:
            return render_template('index.html', error="No valid tax codes found in input or file.")

        def generate_results():
            all_results = []
            yield render_template('stream_header.html', tax_codes_input=', '.join(tax_codes))
            
            # Dynamically determine headers from the first result
            first_result = scrape_tax_code(tax_codes[0])
            all_results.append(first_result)
            
            all_headers = set(first_result.keys())
            ordered_headers = sorted(list(all_headers))
            if 'Code Input' in ordered_headers:
                ordered_headers.insert(0, ordered_headers.pop(ordered_headers.index('Code Input')))
            
            yield render_template('stream_table_header.html', headers=ordered_headers)
            yield render_template('stream_table_row.html', row=first_result, headers=ordered_headers)
            
            # Process remaining codes
            for i, code in enumerate(tax_codes[1:], 1):
                time.sleep(1)
                info = scrape_tax_code(code)
                all_results.append(info)
                all_headers.update(info.keys()) # Update headers in case new fields appear
                yield render_template('stream_table_row.html', row=info, headers=ordered_headers)

            # --- Finalize CSV for download ---
            si = io.StringIO()
            final_headers = sorted(list(all_headers))
            if 'Code Input' in final_headers:
                 final_headers.insert(0, final_headers.pop(final_headers.index('Code Input')))
            writer = csv.DictWriter(si, fieldnames=final_headers)
            writer.writeheader()
            writer.writerows(all_results)
            
            global csv_output
            csv_output = si.getvalue()
            
            yield render_template('stream_footer.html')

        return Response(stream_with_context(generate_results()), mimetype='text/html')

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
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)
