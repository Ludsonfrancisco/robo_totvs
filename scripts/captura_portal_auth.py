#!/usr/bin/env python3
"""Capturar 3 prints do portal dmais autenticado via login."""
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
    page = context.new_page()
    
    # Login primeiro
    print("== Login no portal ==")
    page.goto(f"{BASE}/login/", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    
    # Preencher login
    try:
        page.fill('input[name="email"]', 'ludsoncorrea@gmail.com')
        page.fill('input[type="password"]', 'Dmais@2024')
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_timeout(5000)
        print(f"Login URL após submit: {page.url}")
    except Exception as e:
        print(f"Login erro: {e}")
    
    # Capturar páginas
    for path, name in PAGES:
        url = f"{BASE}{path}"
        print(f"Capturando: {url}")
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        destino = f"/tmp/{name}"
        page.screenshot(path=destino, full_page=True)
        size = os.path.getsize(destino)
        print(f"OK: {destino} ({size} bytes)")
    
    browser.close()
    print("Fim")