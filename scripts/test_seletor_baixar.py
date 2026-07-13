#!/usr/bin/env python3
"""Verificar o HTML exato do link Baixar no LOGA."""
import os, time
from playwright.sync_api import sync_playwright

user = os.environ['ROUTERBOX_USER']
password = os.environ['ROUTERBOX_PASS']
filter_label = os.environ['ROUTERBOX_FILTER_LOGA']

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(accept_downloads=True, viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    
    page.goto('https://integra.loga.net.br/routerbox/app_login/app_login.php', timeout=30000)
    page.wait_for_timeout(3000)
    page.fill('input[name="usuario"]', user)
    page.fill('input[type="password"]', password)
    page.locator('a:has-text("Entrar")').click(timeout=10000)
    page.wait_for_timeout(5000)
    
    try:
        close = page.locator('.modal_menu .closed span').first
        if close.count() and close.is_visible(timeout=1000):
            close.click(timeout=3000)
            page.wait_for_timeout(1000)
    except:
        pass
    
    page.locator('xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]').click(timeout=10000)
    page.wait_for_timeout(1000)
    page.locator('.menu__item:has-text("Atendimentos")').click(timeout=10000)
    page.wait_for_timeout(1000)
    page.locator('#item_59').click(timeout=10000)
    page.wait_for_timeout(5000)
    
    frame2 = page.frames[2]
    frame2.locator('#pesq_top').click(timeout=10000)
    page.wait_for_timeout(3000)
    
    sel = frame2.locator('select#sel_recup_filters_bot').first
    sel.select_option(label=filter_label, timeout=10000)
    page.wait_for_timeout(6000)
    
    frame2.locator('#sc_b_pesq_bot').click(timeout=15000)
    page.wait_for_timeout(10000)
    
    frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
    page.wait_for_timeout(2000)
    
    frame2.locator('#sc_GerarXLS_top').click(timeout=15000)
    print('Clicou Excel, aguardando "Baixar"...')
    
    for i in range(30):
        page.wait_for_timeout(5000)
        elapsed = (i+1) * 5
        
        # Procurar "Baixar" em todos os frames
        for j, f in enumerate(page.frames):
            try:
                # Procurar qualquer elemento com "Baixar"
                resultado = f.evaluate("""() => {
                    const els = document.querySelectorAll('a, button, span, div, p, li');
                    const result = [];
                    els.forEach(el => {
                        const text = (el.textContent || '').trim();
                        if (text === 'Baixar' || text.includes('Baixar')) {
                            result.push({tag: el.tagName, id: el.id, text: text.substring(0, 100), href: el.getAttribute('href') || '', onclick: el.getAttribute('onclick') ? 'yes' : 'no'});
                        }
                    });
                    return result.slice(0, 10);
                }""")
                if resultado:
                    print(f'[{elapsed}s] Frame {j}: {resultado}')
                    # Aguardar mais um pouco e tentar clicar
                    page.wait_for_timeout(2000)
                    
                    # Screenshot
                    page.screenshot(path='/tmp/loga_baixar_found.png', full_page=True)
                    print('Screenshot salvo')
                    browser.close()
                    exit(0)
            except:
                pass
    
    print('Não encontrou Baixar')
    browser.close()