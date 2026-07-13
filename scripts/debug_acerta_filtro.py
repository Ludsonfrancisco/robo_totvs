#!/usr/bin/env python3
"""Debug ACERTA: verificar valores dos campos após carregar filtro."""
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
    
    # Selecionar filtro
    sel = frame2.locator('select#sel_recup_filters_bot').first
    sel.select_option(label=filter_label, timeout=10000)
    page.wait_for_timeout(8000)
    
    # Verificar valores dos campos de data e situação após carregar filtro
    valores = frame2.evaluate("""() => {
        const result = {};
        // Campos de data
        const dateFields = ['atendimentos_data_ab_dia', 'atendimentos_data_ab_mes', 'atendimentos_data_ab_ano',
                           'atendimentos_data_atu_dia', 'atendimentos_data_atu_mes', 'atendimentos_data_atu_ano',
                           'data_dia', 'data_mes', 'data_ano',
                           'agendamento_dia', 'agendamento_mes', 'agendamento_ano'];
        dateFields.forEach(id => {
            const el = document.getElementById(id);
            if (el) result[id] = el.value;
        });
        
        // Condições de data
        const condFields = ['atendimentos_data_ab_cond', 'atendimentos_data_atu_cond', 'data_cond', 'agendamento_cond'];
        condFields.forEach(id => {
            const el = document.getElementById(id);
            if (el) result[id] = el.value;
        });
        
        // Situação checkboxes
        const situacaoChecks = document.querySelectorAll('input[name="atendimentos_situacao[]"]');
        result['situacao_checked'] = [];
        situacaoChecks.forEach(cb => {
            if (cb.checked) result['situacao_checked'].push(cb.value);
        });
        
        // Tipo checkboxes
        const tipoChecks = document.querySelectorAll('input[name="atendimentos_tipo[]"]');
        result['tipo_checked'] = [];
        tipoChecks.forEach(cb => {
            if (cb.checked) result['tipo_checked'].push(cb.value);
        });
        
        return result;
    }""")
    
    print('Valores após carregar filtro:')
    print(json.dumps(valores, indent=2, ensure_ascii=False))
    
    # Screenshot
    page.screenshot(path='/tmp/acerta_filtro_carregado.png', full_page=True)
    print('Screenshot salvo')
    
    browser.close()
