#!/usr/bin/env python3
"""Download ACERTA com willian.demuner interceptando download direto."""
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
    
    print('== Grupo botões ==')
    frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
    page.wait_for_timeout(2000)
    
    print('== Clicar Excel e interceptar download ==')
    try:
        with page.expect_download(timeout=120000) as di:
            frame2.locator('#sc_GerarXLS_top').click(timeout=15000)
            print('Clicou. Aguardando download direto...')
        download = di.value
        destino = '/tmp/acerta_willian_ok.xlsx'
        download.save_as(destino)
        size = os.path.getsize(destino)
        print(f'DOWNLOAD DIRETO OK: {destino} ({size} bytes)')
    except Exception as e:
        print(f'Download direto falhou: {e}')
        
        # Plan B: aguardar link Baixar e clicar
        print('== Plan B: aguardar link Baixar ==')
        for i in range(24):
            page.wait_for_timeout(5000)
            try:
                # Procurar em todos os frames
                for j, f in enumerate(page.frames):
                    baixar = f.locator('a:has-text("Baixar")').first
                    if baixar.count() and baixar.is_visible(timeout=500):
                        print(f'[{(i+1)*5}s] Link Baixar no frame {j}!')
                        with page.expect_download(timeout=60000) as di2:
                            baixar.click(timeout=10000)
                        dl = di2.value
                        dl.save_as('/tmp/acerta_willian_ok.xlsx')
                        print(f'DOWNLOAD VIA BAIXAR OK: {os.path.getsize("/tmp/acerta_willian_ok.xlsx")} bytes')
                        browser.close()
                        exit(0)
            except:
                pass
        
        body = frame2.evaluate("""() => document.body.innerText.substring(0, 500)""")
        print(f'Body final: {body}')
    
    browser.close()
    print('Fim')