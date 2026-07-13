#!/usr/bin/env python3
"""Debug ACERTA: listar campos após selecionar filtro."""
import os, json
from playwright.sync_api import sync_playwright

user = os.environ['ROUTERBOX_USER']
password = os.environ['ROUTERBOX_PASS']
filter_label = os.environ['ROUTERBOX_FILTER_ACERTA']

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
    
    # Listar campos visíveis
    inputs = frame2.evaluate("""() => {
        const result = [];
        document.querySelectorAll('input, select, textarea').forEach(el => {
            const type = el.type || el.tagName.toLowerCase();
            const name = el.name || '';
            const id = el.id || '';
            const value = el.value || '';
            const visible = el.offsetParent !== null;
            if (visible && (name || id)) {
                result.push({type: type, name: name, id: id, value: value.substring(0, 50), tag: el.tagName});
            }
        });
        return result;
    }""")
    
    print('Campos visíveis no frame 2:')
    for inp in inputs:
        t = inp['tag']
        n = inp['name']
        i = inp['id']
        ty = inp['type']
        v = inp['value']
        print(f'  {t} name={n} id={i} type={ty} value={v}')
    
    # Screenshot
    page.screenshot(path='/tmp/acerta_campos.png', full_page=True)
    print('Screenshot salvo')
    
    browser.close()
