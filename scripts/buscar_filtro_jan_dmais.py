#!/usr/bin/env python3
"""Buscar filtro FIELD GERAL JAN DMAIS no ACERTA."""
import os, json
from playwright.sync_api import sync_playwright

user = os.environ['ROUTERBOX_USER']
password = os.environ['ROUTERBOX_PASS']

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(accept_downloads=True, viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    
    page.goto('https://integra.acertasolucoes.net.br/routerbox/app_login/index.php', timeout=30000)
    page.wait_for_timeout(3000)
    page.fill('input[name="usuario"]', user)
    page.fill('input[type="password"]', password)
    page.click('a:has-text("Entrar")')
    page.wait_for_timeout(5000)
    
    try:
        close = page.locator('.modal_menu .closed span').first
        if close.count() and close.is_visible(timeout=1000):
            close.click(timeout=3000)
            page.wait_for_timeout(1000)
    except:
        pass
    
    page.click('xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]')
    page.wait_for_timeout(1000)
    page.click('text=Atendimentos')
    page.wait_for_timeout(1000)
    page.click('#item_59')
    page.wait_for_timeout(5000)
    
    frame2 = page.frames[2]
    frame2.locator('#pesq_top').click(timeout=10000)
    page.wait_for_timeout(3000)
    
    # Buscar filtros com "JAN" e "DMAIS" ou "FIELD"
    options = frame2.evaluate("""() => {
        const s = document.querySelector('select#sel_recup_filters_bot');
        if (!s) return 'NOT_FOUND';
        const result = [];
        for (const o of s.options) {
            const text = (o.textContent || '').trim();
            const upper = text.toUpperCase();
            if (upper.includes('JAN') && upper.includes('DMAIS')) {
                result.push({value: o.value, text: text});
            }
        }
        // Se não achou JAN+DMAIS, buscar só FIELD GERAL
        if (result.length === 0) {
            for (const o of s.options) {
                const text = (o.textContent || '').trim();
                const upper = text.toUpperCase();
                if (upper.includes('FIELD GERAL') && upper.includes('JAN')) {
                    result.push({value: o.value, text: text});
                }
            }
        }
        return result;
    }""")
    
    print(f'Filtros encontrados: {len(options) if isinstance(options, list) else options}')
    if isinstance(options, list):
        for o in options:
            print(f'  VALUE: {repr(o["value"])}')
            print(f'  TEXT:  {repr(o["text"])}')
            print()
    
    browser.close()
