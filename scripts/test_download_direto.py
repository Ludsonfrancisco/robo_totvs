#!/usr/bin/env python3
"""Testar download ACERTA interceptando download direto do browser."""
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
    
    # Screenshot após pesquisa
    page.screenshot(path='/tmp/acerta_jandmais_pos_pesquisa.png', full_page=True)
    
    # Verificar resultado
    info = frame2.evaluate("""() => {
        const body = document.body.innerText;
        const hasError = body.includes('Nenhum dado') || body.includes('Erro ao');
        const rowCount = document.querySelectorAll('table tr').length;
        return {hasError, rowCount, bodyPreview: body.substring(0, 300)};
    }""")
    print(f'hasError={info["hasError"]}, rowCount={info["rowCount"]}')
    
    if info['hasError']:
        print(f'ERRO: {info["bodyPreview"]}')
        browser.close()
        exit(1)
    
    print('== Grupo botões ==')
    frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
    page.wait_for_timeout(2000)
    
    # Screenshot do grupo de botões
    page.screenshot(path='/tmp/acerta_jandmais_botoes.png', full_page=True)
    
    # Listar todas as opções do menu que aparece
    botoes = frame2.evaluate("""() => {
        const result = [];
        document.querySelectorAll('a, button, li, span').forEach(el => {
            const text = (el.textContent || '').trim();
            const id = el.id || '';
            if (text && text.length < 50 && (id.includes('sc_') || id.includes('btgp') || text.includes('Excel') || text.includes('PDF') || text.includes('CSV'))) {
                result.push({text, id, tag: el.tagName, onclick: el.getAttribute('onclick') ? 'yes' : 'no'});
            }
        });
        return result.slice(0, 30);
    }""")
    print('Botões encontrados:')
    for b in botoes:
        print(f'  {b["tag"]} id={b["id"]} text={b["text"]} onclick={b["onclick"]}')
    
    print('== Clicar Excel ==')
    # Intercept download events
    download_started = []
    
    def on_download(dl):
        download_started.append(dl)
        print(f'DOWNLOAD INICIADO: {dl.suggested_filename}')
    
    page.on('download', on_download)
    
    # Também interceptar em todos os frames
    for f in page.frames:
        f.on('download', on_download)
    
    frame2.locator('text=Excel').click(timeout=15000)
    print('Clicou em Excel, aguardando...')
    
    # Aguardar com múltiplas estratégias
    for i in range(36):  # 3 minutos
        page.wait_for_timeout(5000)
        elapsed = (i+1) * 5
        
        # Verificar download direto
        if download_started:
            print(f'[{elapsed}s] DOWNLOAD CAPTURADO!')
            dl = download_started[0]
            dl.save_as('/tmp/acerta_teste_download.xlsx')
            print(f'Arquivo salvo: /tmp/acerta_teste_download.xlsx ({os.path.getsize("/tmp/acerta_teste_download.xlsx")} bytes)')
            break
        
        # Verificar link Baixar
        try:
            baixar = frame2.locator('a:has-text("Baixar")').first
            if baixar.count() and baixar.is_visible(timeout=500):
                print(f'[{elapsed}s] Link Baixar apareceu!')
                with page.expect_download(timeout=60000) as di:
                    baixar.click(timeout=10000)
                download = di.value
                download.save_as('/tmp/acerta_teste_download.xlsx')
                print(f'Arquivo salvo: /tmp/acerta_teste_download.xlsx ({os.path.getsize("/tmp/acerta_teste_download.xlsx")} bytes)')
                break
        except:
            pass
        
        # Verificar link .xlsx
        try:
            xlsx = frame2.locator('a[href*=".xlsx"]').first
            if xlsx.count() and xlsx.is_visible(timeout=500):
                print(f'[{elapsed}s] Link .xlsx apareceu!')
                href = xlsx.get_attribute('href')
                print(f'  href: {href}')
                break
        except:
            pass
        
        if elapsed % 30 == 0:
            page.screenshot(path=f'/tmp/acerta_jandmais_{elapsed}s.png', full_page=True)
            print(f'[{elapsed}s] Screenshot salvo, downloads={len(download_started)}')
    
    browser.close()
    print('Fim')