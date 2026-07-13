#!/usr/bin/env python3
"""Teste ACERTA com debug completo."""
import os, time
from playwright.sync_api import sync_playwright

user = os.environ['ROUTERBOX_USER']
password = os.environ['ROUTERBOX_PASS']
filter_label = os.environ['ROUTERBOX_FILTER_ACERTA']

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(accept_downloads=True, viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    
    print('== Login ==')
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
    
    print('== Navegar ==')
    page.click('xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]')
    page.wait_for_timeout(1000)
    page.click('text=Atendimentos')
    page.wait_for_timeout(1000)
    page.click('#item_59')
    page.wait_for_timeout(5000)
    
    print('== Pesquisar topo ==')
    frame2 = page.frames[2]
    frame2.locator('#pesq_top').click(timeout=10000)
    page.wait_for_timeout(3000)
    
    print(f'== Selecionar filtro: {filter_label} ==')
    sel = frame2.locator('select#sel_recup_filters_bot').first
    sel.select_option(label=filter_label, timeout=10000)
    page.wait_for_timeout(6000)
    
    print('== Pesquisar rodapé ==')
    frame2.locator('#sc_b_pesq_bot').click(timeout=15000)
    page.wait_for_timeout(10000)
    
    # Screenshot após pesquisar
    page.screenshot(path='/tmp/acerta_pos_pesquisa.png', full_page=True)
    print('Screenshot após pesquisa salvo')
    
    # Verificar se há mensagem de erro ou dados
    conteudo = frame2.evaluate("""() => {
        const body = document.body.innerText;
        const hasError = body.includes('Nenhum dado') || body.includes('Erro');
        const hasTable = document.querySelector('table') !== null;
        const rowCount = document.querySelectorAll('table tr').length;
        return {hasError, hasTable, rowCount, bodyPreview: body.substring(0, 500)};
    }""")
    
    print(f'Conteúdo: hasError={conteudo["hasError"]}, hasTable={conteudo["hasTable"]}, rowCount={conteudo["rowCount"]}')
    if conteudo['hasError']:
        print('ERRO detectado na página')
    
    print('== Grupo botões ==')
    frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
    page.wait_for_timeout(2000)
    
    print('== Excel ==')
    frame2.locator('text=Excel').click(timeout=15000)
    print('Clicou em Excel, aguardando link Baixar...')
    
    # Aguardar link Baixar com timeout de 120s
    for i in range(24):
        page.wait_for_timeout(5000)
        try:
            baixar = frame2.locator('a:has-text("Baixar")').first
            if baixar.count() and baixar.is_visible(timeout=500):
                print(f'[{(i+1)*5}s] Link Baixar apareceu!')
                break
        except:
            pass
        
        if (i+1) % 6 == 0:
            page.screenshot(path=f'/tmp/acerta_espera_{(i+1)*5}s.png')
            print(f'[{(i+1)*5}s] Screenshot salvo')
    
    browser.close()
    print('Fim')
