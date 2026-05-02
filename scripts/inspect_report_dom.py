import time
from playwright.sync_api import sync_playwright
from core.config import settings
from core.log import log
from core.acoes import fazer_login, navegar_ate_rotina

def inspect_report_screen():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()
        
        try:
            fazer_login(page)
            navegar_ate_rotina(page)
            
            # Chegar no ponto de falha (Passo 11 e 12)
            from core.visao import clicar_imagem
            clicar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=15)
            page.keyboard.type("HK")
            page.keyboard.press("Enter")
            time.sleep(5)
            
            # Clicar Planilha
            for ctx in [page, *page.frames]:
                try:
                    loc = ctx.locator('text="Planilha"').first
                    if loc.is_visible(timeout=2000):
                        loc.click()
                        print(f"Clicou Planilha no frame: {ctx.url}")
                        break
                except: continue
            
            time.sleep(3)
            
            print("--- INSPEÇÃO DE SELECTS ---")
            for i, frame in enumerate(page.frames):
                selects = frame.locator("select").all()
                print(f"Frame {i} ({frame.url}): {len(selects)} selects")
                for s in selects:
                    print(f"  - HTML: {s.evaluate('el => el.outerHTML')}")
                    print(f"  - Value: {s.evaluate('el => el.value')}")
                    options = s.locator("option").all()
                    for opt in options:
                        print(f"    - Option: {opt.evaluate('el => el.outerHTML')}")
            
            time.sleep(10)
        finally:
            browser.close()

if __name__ == "__main__":
    inspect_report_screen()
