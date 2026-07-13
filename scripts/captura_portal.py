#!/usr/bin/env python3
"""Capturar 3 prints do portal dmais para o Giro."""
import os, time
from playwright.sync_api import sync_playwright

BASE = "https://dmais.palmbook.online"
PAGES = [
    ("/", "giro_dashboard.png"),
    ("/estoque/", "giro_estoque.png"),
    ("/prazo-atendimento/", "giro_prazo.png"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(viewport={'width': 1366, 'height': 900}, ignore_https_errors=True)
    
    for path, name in PAGES:
        page = context.new_page()
        url = f"{BASE}{path}"
        print(f"Capturando: {url}")
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        destino = f"/tmp/{name}"
        page.screenshot(path=destino, full_page=True)
        size = os.path.getsize(destino)
        print(f"OK: {destino} ({size} bytes)")
        page.close()
    
    browser.close()
    print("Fim")