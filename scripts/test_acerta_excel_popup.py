#!/usr/bin/env python3
"""Testar ACERTA - monitorar popups e novas janelas após clicar Excel."""
import os, time
from playwright.sync_api import sync_playwright

user = os.environ['ROUTERBOX_USER']
password = os.environ['ROUTERBOX_PASS']
filter_label = os.environ['ROUTERBOX_FILTER_ACERTA']

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    context = browser.new_context(accept_downloads=True, viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    
    # Monitorar popups
    popups = []
    def on_popup(popup):
        popups.append(popup)
        print(f'POPUP detectado: {popup.url[:100]}')
    page.on('popup', on_popup)
    
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
    
    # Verificar resultado
    info = frame2.evaluate("""() => {
        const body = document.body.innerText;
        const hasError = body.includes('Nenhum dado') || body.includes('Erro ao');
        const rowCount = document.querySelectorAll('table tr').length;
        return {hasError, rowCount};
    }""")
    print(f'hasError={info["hasError"]}, rowCount={info["rowCount"]}')
    
    if not info['hasError'] and info['rowCount'] > 0:
        print('== Grupo botões ==')
        frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
        page.wait_for_timeout(2000)
        
        # Verificar onclick do botão Excel
        onclick = frame2.evaluate("""() => {
            const btn = document.getElementById('sc_GerarXLS_top');
            return btn ? btn.getAttribute('onclick') : 'NOT_FOUND';
        }""")
        print(f'Onclick do botão Excel: {onclick}')
        
        # Screenshot antes do clique
        page.screenshot(path='/tmp/acerta_antes_excel.png', full_page=True)
        
        print('== Clicar Excel ==')
        frame2.locator('#sc_GerarXLS_top').click(timeout=15000)
        print('Clicou. Aguardando...')
        
        # Aguardar e monitorar
        for i in range(24):
            page.wait_for_timeout(5000)
            elapsed = (i+1) * 5
            print(f'[{elapsed}s] popups={len(popups)}, frames={len(page.frames)}')
            
            # Verificar mensagem no frame
            try:
                body = frame2.evaluate("() => document.body.innerText.substring(0, 300)")
                if 'Baixar' in body:
                    print(f'  "Baixar" encontrado no body!')
                    # Procurar link Baixar
                    baixar = frame2.locator('a:has-text("Baixar")').first
                    if baixar.count():
                        print('  Link Baixar visível!')
                if 'Erro' in body:
                    print(f'  Erro: {body[:200]}')
                    break
            except:
                pass
            
            # Verificar novos frames
            if len(page.frames) > 11:
                print(f'  Novo frame detectado! Total: {len(page.frames)}')
                for j, f in enumerate(page.frames):
                    print(f'    Frame {j}: {f.url[:100]}')
            
            if elapsed % 30 == 0:
                page.screenshot(path=f'/tmp/acerta_excel_{elapsed}s.png', full_page=True)
    
    browser.close()
    print('Fim')