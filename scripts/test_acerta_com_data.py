#!/usr/bin/env python3
"""Testar ACERTA preenchendo data antes de exportar Excel."""
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
    
    # Verificar valores atuais do filtro
    valores = frame2.evaluate("""() => {
        const result = {};
        // Data AB
        const cond = document.getElementById('SC_atendimentos_data_ab_cond');
        result['data_ab_cond'] = cond ? cond.value : 'N/A';
        result['data_ab_dia'] = document.getElementById('SC_atendimentos_data_ab_dia')?.value || '';
        result['data_ab_mes'] = document.getElementById('SC_atendimentos_data_ab_mes')?.value || '';
        result['data_ab_ano'] = document.getElementById('SC_atendimentos_data_ab_ano')?.value || '';
        
        // Data Atualização
        const condAtu = document.getElementById('SC_atendimentos_data_atu_cond');
        result['data_atu_cond'] = condAtu ? condAtu.value : 'N/A';
        result['data_atu_dia'] = document.getElementById('SC_atendimentos_data_atu_dia')?.value || '';
        result['data_atu_mes'] = document.getElementById('SC_atendimentos_data_atu_mes')?.value || '';
        result['data_atu_ano'] = document.getElementById('SC_atendimentos_data_atu_ano')?.value || '';
        
        // Situação
        const sit = document.querySelectorAll('input[name="atendimentos_situacao[]"]');
        result['situacao'] = [];
        sit.forEach(cb => { if (cb.checked) result['situacao'].push(cb.value); });
        
        // Tipo
        const tipo = document.querySelectorAll('input[name="atendimentos_tipo[]"]');
        result['tipo'] = [];
        tipo.forEach(cb => { if (cb.checked) result['tipo'].push(cb.value); });
        
        // Situação OS
        const sitOs = document.querySelectorAll('input[name="atendimentos_situacaoos[]"]');
        result['situacao_os'] = [];
        sitOs.forEach(cb => { if (cb.checked) result['situacao_os'].push(cb.value); });
        
        return result;
    }""")
    print(f'Valores do filtro: {valores}')
    
    # Preencher Data AB: maior ou igual a 01/01/2026
    print('== Preenchendo Data AB >= 01/01/2026 ==')
    frame2.evaluate("""() => {
        const cond = document.getElementById('SC_atendimentos_data_ab_cond');
        if (cond) cond.value = 'ge';
        const dia = document.getElementById('SC_atendimentos_data_ab_dia');
        if (dia) dia.value = '1';
        const mes = document.getElementById('SC_atendimentos_data_ab_mes');
        if (mes) mes.value = '1';
        const ano = document.getElementById('SC_atendimentos_data_ab_ano');
        if (ano) ano.value = '2026';
    }""")
    page.wait_for_timeout(1000)
    
    print('== Pesquisar rodapé ==')
    frame2.locator('#sc_b_pesq_bot').click(timeout=15000)
    page.wait_for_timeout(10000)
    
    # Verificar resultado
    info = frame2.evaluate("""() => {
        const body = document.body.innerText;
        const hasError = body.includes('Nenhum dado') || body.includes('Erro ao');
        const rowCount = document.querySelectorAll('table tr').length;
        return {hasError, rowCount, bodyPreview: body.substring(0, 500)};
    }""")
    print(f'Após data: hasError={info["hasError"]}, rowCount={info["rowCount"]}')
    if info['hasError']:
        print(f'Body: {info["bodyPreview"]}')
    
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
            destino = '/tmp/acerta_jandmais_com_data.xlsx'
            download.save_as(destino)
            size = os.path.getsize(destino)
            print(f'DOWNLOAD OK: {destino} ({size} bytes)')
        except Exception as e:
            print(f'Download falhou: {e}')
            # Verificar mensagem
            body = frame2.evaluate("""() => document.body.innerText.substring(0, 500)""")
            print(f'Body: {body}')
            page.screenshot(path='/tmp/acerta_com_data_final.png', full_page=True)
    
    browser.close()
    print('Fim')