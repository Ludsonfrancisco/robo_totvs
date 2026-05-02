from playwright.sync_api import sync_playwright
from core.acoes import fazer_login, navegar_ate_rotina

def debug():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()
        
        try:
            fazer_login(page)
            navegar_ate_rotina(page)
            print("Robô chegou à tela de rotina. Assuma o controle no navegador.")
            print("Pausando a automação. Você pode interagir com o navegador agora.")
            page.pause()
        finally:
            browser.close()

if __name__ == "__main__":
    debug()
