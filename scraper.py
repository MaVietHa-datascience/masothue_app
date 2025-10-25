import requests
from bs4 import BeautifulSoup
import csv

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
            # Add the original tax code for reference
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

if __name__ == '__main__':
    tax_codes_input = input("Please enter tax codes, separated by commas: ")
    # Split by comma and remove any empty strings or extra whitespace
    tax_codes = [code.strip() for code in tax_codes_input.split(',') if code.strip()]
    
    if not tax_codes:
        print("No valid tax codes were entered.")
    else:
        all_results = []
        print(f"\\nFound {len(tax_codes)} tax code(s). Starting scrape...")
        print("-" * 30)
        
        for i, code in enumerate(tax_codes, 1):
            print(f"({i}/{len(tax_codes)}) Scraping tax code: {code}...")
            info = scrape_tax_code(code)
            if info:
                all_results.append(info)
        
        print("-" * 30)
        print("Scraping complete.")

        if all_results:
            # --- Save to CSV ---
            # Get all unique headers from all results to create a complete set of columns
            all_headers = set()
            for result in all_results:
                all_headers.update(result.keys())
            
            # Define a preferred order, with the input code first
            ordered_headers = sorted(list(all_headers))
            if 'Tax Code Input' in ordered_headers:
                ordered_headers.insert(0, ordered_headers.pop(ordered_headers.index('Tax Code Input')))

            try:
                with open('results.csv', 'w', newline='', encoding='utf-8-sig') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=ordered_headers)
                    writer.writeheader()
                    writer.writerows(all_results)
                print("\\nSuccessfully saved all data to results.csv")
            except IOError as e:
                print(f"\\nError: Could not write to results.csv. Reason: {e}")
