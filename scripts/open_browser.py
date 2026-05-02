from playwright.sync_api import sync_playwright
from core.config import settings

def debug():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()
        page.goto(settings.PROTHEUS_URL)
        
        print("Navegador aberto em:", settings.PROTHEUS_URL)
        print("Pausando a automação. Você pode testar livremente.")
        page.pause()
        
if __name__ == "__main__":
    debug()
