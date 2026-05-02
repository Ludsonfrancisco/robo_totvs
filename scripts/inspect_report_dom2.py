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
            page.keyboard.type("HK", delay=50)
            time.sleep(1)
            
            # Clicar OK
            clicou_ok = False
            for ctx in [page, *page.frames]:
                for sel in ['button:has-text("OK"):not([disabled])', 'button:has-text("OK")', '[title="OK"]']:
                    try:
                        loc = ctx.locator(sel).last
                        if loc.is_visible(timeout=500):
                            loc.click()
                            print("Clicou OK via DOM")
                            clicou_ok = True
                            break
                    except: continue
                if clicou_ok: break
            
            if not clicou_ok:
                clicou_ok = clicar_imagem(page, "12_clicar_OK.png", timeout=15)
                print(f"Clicou OK via imagem: {clicou_ok}")
            
            time.sleep(5)
            
            # Clicar Planilha
            clicou_planilha = False
            for ctx in [page, *page.frames]:
                for sel in ['text="Planilha"', 'span:has-text("Planilha")', 'a:has-text("Planilha")']:
                    try:
                        loc = ctx.locator(sel).first
                        if loc.is_visible(timeout=500):
                            loc.click()
                            print(f"Clicou Planilha via {sel}")
                            clicou_planilha = True
                            break
                    except: continue
                if clicou_planilha: break
            
            if not clicou_planilha:
                clicou_planilha = clicar_imagem(page, "13_clicar_planilha.png", timeout=15)
                print(f"Clicou Planilha via imagem: {clicou_planilha}")
            
            time.sleep(3)
            
            print("--- INSPEÇÃO DE SELECTS ---")
            for i, frame in enumerate([page, *page.frames]):
                selects = frame.locator("select").all()
                print(f"Frame {i} ({frame.url}): {len(selects)} selects")
                for s in selects:
                    print(f"  - HTML: {s.evaluate('el => el.outerHTML')}")
            
            time.sleep(5)
        finally:
            browser.close()

if __name__ == "__main__":
    inspect_report_screen()
