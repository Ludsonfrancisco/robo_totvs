#!/usr/bin/env python3
"""Testar download ACERTA clicando no botão pelo ID e interceptando download."""
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
    
    if info['hasError']:
        print('ERRO: dados não encontrados')
        browser.close()
        exit(1)
    
    print('== Grupo botões ==')
    frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
    page.wait_for_timeout(2000)
    
    print('== Clicar Excel via ID sc_GerarXLS_top ==')
    
    # Tentar interceptar download com expect_download
    try:
        with page.expect_download(timeout=120000) as di:
            # Clicar via ID no frame2
            frame2.locator('#sc_GerarXLS_top').click(timeout=15000)
            print('Clicou. Aguardando download...')
        download = di.value
        destino = '/tmp/acerta_jandmais.xlsx'
        download.save_as(destino)
        size = os.path.getsize(destino)
        print(f'DOWNLOAD OK: {destino} ({size} bytes)')
    except Exception as e:
        print(f'expect_download falhou: {e}')
        
        # Plan B: verificar se um iframe de download apareceu
        print('== Plan B: procurar iframe/link de download ==')
        for i in range(12):
            page.wait_for_timeout(5000)
            print(f'[{(i+1)*5}s] Verificando...')
            
            # Procurar link Baixar em todos os frames
            for j, f in enumerate(page.frames):
                try:
                    baixar = f.locator('a:has-text("Baixar")').first
                    if baixar.count() and baixar.is_visible(timeout=500):
                        print(f'  Link Baixar no frame {j}!')
                        with page.expect_download(timeout=60000) as di2:
                            baixar.click(timeout=10000)
                        dl = di2.value
                        dl.save_as('/tmp/acerta_jandmais.xlsx')
                        print(f'DOWNLOAD OK: {os.path.getsize("/tmp/acerta_jandmais.xlsx")} bytes')
                        browser.close()
                        exit(0)
                except:
                    pass
            
            # Procurar iframe novo
            for j, f in enumerate(page.frames):
                try:
                    url = f.url
                    if '.xlsx' in url or 'download' in url.lower() or 'gerar' in url.lower():
                        print(f'  Frame {j} com URL suspeita: {url[:100]}')
                except:
                    pass
            
            if (i+1) % 3 == 0:
                page.screenshot(path=f'/tmp/acerta_btn_id_{(i+1)*5}s.png', full_page=True)
        
        # Verificar conteúdo do frame após clique
        print('== Verificando conteúdo do frame após clique ==')
        body = frame2.evaluate("""() => document.body.innerText.substring(0, 1000)""")
        print(f'Body: {body[:500]}')
        
        # Screenshot final
        page.screenshot(path='/tmp/acerta_btn_id_final.png', full_page=True)
    
    browser.close()
    print('Fim')