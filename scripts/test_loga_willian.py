#!/usr/bin/env python3
"""Testar LOGA com willian.demuner - debug do Excel."""
import os, time
from playwright.sync_api import sync_playwright

user = os.environ['ROUTERBOX_USER']
password = os.environ['ROUTERBOX_PASS']
filter_label = os.environ['ROUTERBOX_FILTER_LOGA']

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(accept_downloads=True, viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    
    print('== Login LOGA ==')
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
    
    print('== Navegar ==')
    page.locator('xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]').click(timeout=10000)
    page.wait_for_timeout(1000)
    page.locator('.menu__item:has-text("Atendimentos")').click(timeout=10000)
    page.wait_for_timeout(1000)
    page.locator('#item_59').click(timeout=10000)
    page.wait_for_timeout(5000)
    
    frame2 = page.frames[2]
    print('== Pesquisar topo ==')
    frame2.locator('#pesq_top').click(timeout=10000)
    page.wait_for_timeout(3000)
    
    print(f'== Filtro: {filter_label} ==')
    sel = frame2.locator('select#sel_recup_filters_bot').first
    sel.select_option(label=filter_label, timeout=10000)
    page.wait_for_timeout(6000)
    
    print('== Pesquisar rodapé ==')
    frame2.locator('#sc_b_pesq_bot').click(timeout=15000)
    page.wait_for_timeout(10000)
    
    info = frame2.evaluate("""() => {
        const body = document.body.innerText;
        const hasError = body.includes('Nenhum dado') || body.includes('Erro ao');
        const rowCount = document.querySelectorAll('table tr').length;
        return {hasError, rowCount, body: body.substring(0, 300)};
    }""")
    print(f'Pesquisa: hasError={info["hasError"]}, rowCount={info["rowCount"]}')
    
    if not info['hasError'] and info['rowCount'] > 0:
        print('== Grupo botões ==')
        frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
        page.wait_for_timeout(2000)
        
        print('== Clicar Excel ==')
        frame2.locator('#sc_GerarXLS_top').click(timeout=15000)
        print('Clicou. Aguardando 15s...')
        page.wait_for_timeout(15000)
        
        body = frame2.evaluate("""() => document.body.innerText.substring(0, 500)""")
        print(f'Body após Excel: {body}')
        
        if 'Baixar' in body:
            print('LINK BAIXAR ENCONTRADO!')
        
        page.screenshot(path='/tmp/loga_willian.png', full_page=True)
        print('Screenshot salvo')
    else:
        print(f'Erro: {info["body"]}')
    
    browser.close()
    print('Fim')