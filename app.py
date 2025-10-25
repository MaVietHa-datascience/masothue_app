import requests
from flask import Flask, render_template, request, make_response
from bs4 import BeautifulSoup
import csv
import io
import pandas as pd
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'


def scrape_tax_code_with_selenium(tax_code):
    """
    Scrapes masothue.com for a given tax code using Selenium.
    """
    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')  # headless mode mới, ổn định hơn
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--blink-settings=imagesEnabled=false')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--log-level=3')
        options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
        )

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)

        # --- Perform the scraping ---
        driver.get('https://masothue.com/')
        search_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/nav/div/form/div/input'))
        )
        search_input.send_keys(tax_code)
        search_button = driver.find_element(By.XPATH, '/html/body/div[1]/nav/div/form/div/div/button')
        search_button.click()

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'table-taxinfo'))
        )

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        table = soup.find('table', class_='table-taxinfo')

        if table:
            company_info = {'Tax Code Input': tax_code}
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True).replace(':', '').strip()
                    value = cells[1].get_text(strip=True)
                    company_info[key] = value
            print(f"Successfully scraped data for {tax_code}.")
            return company_info
        else:
            return {'Tax Code Input': tax_code, 'Status': 'Information not found'}

    except Exception as e:
        print(f"Error while scraping {tax_code}: {e}")
        return {'Tax Code Input': tax_code, 'Status': f'Scraping Error: {e}'}
    finally:
        if driver:
            driver.quit()


def is_potential_tax_code(value):
    """Checks if a value is likely a tax code."""
    if not isinstance(value, str):
        return False
    pattern = re.compile(r'^\d{8,15}(-\d{3})?$')
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

        all_results = []

        # --- Run in parallel ---
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_code = {executor.submit(scrape_tax_code_with_selenium, code): code for code in tax_codes}

            for i, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    info = future.result()
                    if info:
                        all_results.append(info)
                    print(f"({i+1}/{len(tax_codes)}) Completed scraping for {code}")
                except Exception as exc:
                    print(f"Error processing {code}: {exc}")
                    all_results.append({'Tax Code Input': code, 'Status': f'Error: {exc}'})

        if all_results:
            si = io.StringIO()
            all_headers = set().union(*(d.keys() for d in all_results))
            ordered_headers = sorted(list(all_headers))
            if 'Tax Code Input' in ordered_headers:
                ordered_headers.insert(0, ordered_headers.pop(ordered_headers.index('Tax Code Input')))

            writer = csv.DictWriter(si, fieldnames=ordered_headers)
            writer.writeheader()
            writer.writerows(all_results)

            global csv_output
            csv_output = si.getvalue()

            return render_template('index.html', results=all_results, headers=ordered_headers, tax_codes_input=', '.join(tax_codes))

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
