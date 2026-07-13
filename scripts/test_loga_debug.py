#!/usr/bin/env python3
"""Comparar LOGA (funciona) vs ACERTA (falha) - verificar diferenças no Excel."""
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
    
    print(f'== Filtro LOGA: {filter_label} ==')
    sel = frame2.locator('select#sel_recup_filters_bot').first
    sel.select_option(label=filter_label, timeout=10000)
    page.wait_for_timeout(6000)
    
    # Verificar valores do filtro LOGA
    valores = frame2.evaluate("""() => {
        const result = {};
        const cond = document.getElementById('SC_atendimentos_data_ab_cond');
        result['data_ab_cond'] = cond ? cond.value : 'N/A';
        result['data_ab_dia'] = document.getElementById('SC_atendimentos_data_ab_dia')?.value || '';
        result['data_ab_mes'] = document.getElementById('SC_atendimentos_data_ab_mes')?.value || '';
        result['data_ab_ano'] = document.getElementById('SC_atendimentos_data_ab_ano')?.value || '';
        result['data_atu_cond'] = document.getElementById('SC_atendimentos_data_atu_cond')?.value || 'N/A';
        result['data_atu_dia'] = document.getElementById('SC_atendimentos_data_atu_dia')?.value || '';
        result['data_atu_mes'] = document.getElementById('SC_atendimentos_data_atu_mes')?.value || '';
        result['data_atu_ano'] = document.getElementById('SC_atendimentos_data_atu_ano')?.value || '';
        result['data_cond'] = document.getElementById('SC_data_cond')?.value || 'N/A';
        result['data_dia'] = document.getElementById('SC_data_dia')?.value || '';
        result['data_mes'] = document.getElementById('SC_data_mes')?.value || '';
        result['data_ano'] = document.getElementById('SC_data_ano')?.value || '';
        
        const sit = document.querySelectorAll('input[name="atendimentos_situacao[]"]');
        result['situacao'] = [];
        sit.forEach(cb => { if (cb.checked) result['situacao'].push(cb.value); });
        
        const tipo = document.querySelectorAll('input[name="atendimentos_tipo[]"]');
        result['tipo'] = [];
        tipo.forEach(cb => { if (cb.checked) result['tipo'].push(cb.value); });
        
        const sitOs = document.querySelectorAll('input[name="atendimentos_situacaoos[]"]');
        result['situacao_os'] = [];
        sitOs.forEach(cb => { if (cb.checked) result['situacao_os'].push(cb.value); });
        
        return result;
    }""")
    print(f'Valores do filtro LOGA: {valores}')
    
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
    print(f'LOGA: hasError={info["hasError"]}, rowCount={info["rowCount"]}')
    
    if not info['hasError'] and info['rowCount'] > 0:
        print('== Grupo botões ==')
        frame2.locator('#sc_btgp_btn_group_1_top').click(timeout=15000)
        page.wait_for_timeout(2000)
        
        print('== Clicar Excel ==')
        try:
            with page.expect_download(timeout=180000) as di:
                frame2.locator('#sc_GerarXLS_top').click(timeout=15000)
                print('Clicou. Aguardando download...')
            download = di.value
            destino = '/tmp/loga_teste.xlsx'
            download.save_as(destino)
            size = os.path.getsize(destino)
            print(f'DOWNLOAD LOGA OK: {destino} ({size} bytes)')
        except Exception as e:
            print(f'Download LOGA falhou: {e}')
            body = frame2.evaluate("""() => document.body.innerText.substring(0, 500)""")
            print(f'Body: {body}')
    
    browser.close()
    print('Fim')