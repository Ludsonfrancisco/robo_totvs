import time
import os
from playwright.sync_api import sync_playwright
from core.config import settings
from core.log import log
from core.acoes import fazer_login, navegar_ate_rotina, clicar_imagem

def inspect_passo_17():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()
        
        try:
            fazer_login(page)
            navegar_ate_rotina(page)
            
            # Chegar no ponto de filtro (Passo 11 e 12)
            clicar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=15)
            page.keyboard.type("HK")
            page.keyboard.press("Enter")
            time.sleep(5)
            
            # Clicar Planilha (Passo 13)
            for ctx in [page, *page.frames]:
                try:
                    loc = ctx.locator('text="Planilha"').first
                    if loc.is_visible(timeout=2000):
                        loc.click()
                        print(f"Clicou Planilha no frame: {ctx.url}")
                        break
                except: continue
            
            time.sleep(2)
            
            # Selecionar Tipo 3 (Passo 14)
            for ctx in [page, *page.frames]:
                try:
                    sel = ctx.locator("select").first
                    if sel.is_visible(timeout=2000):
                        sel.select_option(value="3")
                        print(f"Selecionou Tipo 3 no frame: {ctx.url}")
                        break
                except: continue
            
            time.sleep(2)
            
            # Clicar Imprimir (Passo 16)
            for ctx in [page, *page.frames]:
                try:
                    loc = ctx.locator('button:has-text("Imprimir")').first
                    if loc.is_visible(timeout=2000):
                        loc.click()
                        print(f"Clicou Imprimir no frame: {ctx.url}")
                        break
                except: continue
            
            time.sleep(5) # Aguarda popup "Sim/Não"
            
            page.screenshot(path="inspect_passo_17.png")
            print("Screenshot inspect_passo_17.png salvo.")
            
            print("--- INSPEÇÃO DE ELEMENTOS NO POPUP ---")
            for i, frame in enumerate(page.frames):
                print(f"\nFrame {i} ({frame.url}):")
                # Procurar qualquer coisa com "Sim"
                sim_elements = frame.locator('*:has-text("Sim")').all()
                print(f"  - Elementos com 'Sim': {len(sim_elements)}")
                for el in sim_elements[:5]: # Mostrar os 5 primeiros
                    tag = el.evaluate('el => el.tagName')
                    html = el.evaluate('el => el.outerHTML')
                    print(f"    - [{tag}]: {html[:200]}...")
                
                # Procurar botões e selects
                btns = frame.locator('button').all()
                print(f"  - Botões: {len(btns)}")
                for b in btns:
                    print(f"    - Botão: {b.inner_text()} | {b.evaluate('el => el.outerHTML')[:100]}")
                
                selects = frame.locator('select').all()
                print(f"  - Selects: {len(selects)}")
                for s in selects:
                    print(f"    - Select: {s.evaluate('el => el.outerHTML')[:100]}")
            
            time.sleep(15)
        finally:
            browser.close()

if __name__ == "__main__":
    os.environ["PYTHONPATH"] = "."
    inspect_passo_17()
