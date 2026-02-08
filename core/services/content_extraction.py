import re

import requests
from bs4 import BeautifulSoup


def fetch_and_clean(url, timeout=15):
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "GroitAI/1.0"})
    response.raise_for_status()
    html = response.text

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav', 'aside']):
        tag.decompose()

    text = soup.get_text(separator='\n')
    text = re.sub(r'\n{2,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()
