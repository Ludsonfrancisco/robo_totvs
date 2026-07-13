#!/usr/bin/env python3
"""Listar filtros FIELD GERAL no ACERTA."""
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
    
    # Listar TODOS os options que contêm FIELD
    options = frame2.evaluate("""() => {
        const s = document.querySelector('select#sel_recup_filters_bot');
        if (!s) return 'NOT_FOUND';
        const result = [];
        for (const o of s.options) {
            const text = (o.textContent || '').trim();
            if (text.toUpperCase().includes('FIELD') || text.toUpperCase().includes('AJN') || text.toUpperCase().includes('JAN')) {
                result.push({value: o.value, text: text});
            }
        }
        return result;
    }""")
    
    print('Filtros contendo FIELD/AJN/JAN:')
    for o in options:
        v = o['value']
        t = o['text']
        # Mostrar caracteres especiais
        print(f'  VALUE: {repr(v)}')
        print(f'  TEXT:  {repr(t)}')
        print()
    
    browser.close()
