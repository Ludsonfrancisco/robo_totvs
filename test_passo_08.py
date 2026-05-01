from core.config import settings
from core.log import log
from core.navegador import iniciar_navegador, tirar_screenshot
from core.acoes import fazer_login
from core.visao import clicar_imagem
import time

def main():
    log.bind(etapa="teste").info("Iniciando teste até o passo 08")
    with iniciar_navegador() as (_, _, page):
        fazer_login(page)
        
        # Passo 07: Abrir favoritos
        log.bind(etapa="teste").info("Aguardando e clicando em Favoritos (07)...")
        clicou_fav = False
        
        seletores_fav = [
            'text="Favoritos"',
            '[aria-label="Favoritos"]',
            '[title="Favoritos"]',
            '.wa-menu-item:has-text("Favoritos")',
            'po-menu-item:has-text("Favoritos")',
            'a:has-text("Favoritos")'
        ]
        
        for ctx in [page, *page.frames]:
            for sel in seletores_fav:
                try:
                    loc = ctx.locator(sel).first
                    if loc.is_visible(timeout=500):
                        loc.click()
                        log.bind(etapa="teste").info(f"Clicou em Favoritos via DOM ({sel})")
                        clicou_fav = True
                        break
                except Exception:
                    continue
            if clicou_fav:
                break
                
        if not clicou_fav:
            clicou_fav = clicar_imagem(page, "07_pagina_home_clicar_favoritos.png", timeout=15, threshold=0.65)
            
        if not clicou_fav:
            log.bind(etapa="teste").error("Falha ao clicar em Favoritos")
            tirar_screenshot(page, etapa="falha_07", evidencia=True)
            return 1
            
        time.sleep(2) # Aguarda animação do menu
        
        # Passo 08: Abrir relatório
        log.bind(etapa="teste").info("Aguardando e clicando em Mat Estoque Por Tecnico (08)...")
        clicou_rel = False
        
        for ctx in [page, *page.frames]:
            try:
                loc = ctx.locator('text="Mat Estoque Por Tecnico"').first
                if loc.is_visible(timeout=1000):
                    loc.click()
                    log.bind(etapa="teste").info("Clicou no relatório via DOM")
                    clicou_rel = True
                    break
            except Exception:
                pass
                
        if not clicou_rel:
            clicou_rel = clicar_imagem(page, "08_clicar_Mat_Estoque_Por_Tecnico.png", timeout=15, threshold=0.65)
            
        if not clicou_rel:
            log.bind(etapa="teste").error("Falha ao clicar no relatório")
            tirar_screenshot(page, etapa="falha_08", evidencia=True)
            return 1
            
        time.sleep(5) # Aguarda carregamento inicial da próxima tela
        screenshot_path = tirar_screenshot(page, etapa="sucesso_passo_08", evidencia=False)
        log.bind(etapa="teste").success(f"Passo 08 alcançado com sucesso! Screenshot salva em: {screenshot_path}")
        
    return 0

if __name__ == "__main__":
    raise SystemExit(main())