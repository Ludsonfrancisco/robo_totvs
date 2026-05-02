import os
import csv
from typing import Optional
from playwright.sync_api import Page, TimeoutError
from core.log import log

class ProtheusDownloadStrategies:
    """
    Classe contendo estratégias para contornar o bloqueio de download (Ambiente "Servidor") 
    no TOTVS Protheus WebApp.
    """

    @staticmethod
    def intercept_network_binary(page: Page, output_path: str) -> bool:
        """
        Estratégia 1: Injeção/Interceptação de Rede (Network Interception).
        """
        log.bind(etapa="estrategia").info("Iniciando monitoramento de rede (Network Interception)...")
        download_success = False

        def handle_response(response):
            nonlocal download_success
            content_type = response.header_value('content-type') or ''
            url_lower = response.url.lower()
            
            if 'spreadsheetml' in content_type or 'ms-excel' in content_type or 'download' in url_lower:
                try:
                    body = response.body()
                    if len(body) > 0:
                        with open(output_path, 'wb') as file:
                            file.write(body)
                        download_success = True
                        log.bind(etapa="estrategia").info(f"Sucesso! Binário capturado: {response.url}")
                except Exception as e:
                    log.bind(etapa="estrategia").error(f"Falha ao ler binário: {e}")

        page.on('response', handle_response)
        try:
            page.locator('button:has-text("Imprimir")').last.click()
            page.wait_for_timeout(10000)
        finally:
            page.remove_listener('response', handle_response)
        return download_success

    @staticmethod
    def monitor_hidden_popup(page: Page, output_dir: str) -> Optional[str]:
        """
        Estratégia 2: Monitoramento de Pop-ups Ocultos.
        """
        log.bind(etapa="estrategia").info("Monitorando popups ocultos (Hidden Pop-ups)...")
        try:
            with page.expect_popup(timeout=8000) as popup_info:
                page.locator('button:has-text("Imprimir")').last.click()
            
            popup = popup_info.value
            popup.wait_for_load_state('domcontentloaded')
            log.bind(etapa="estrategia").info(f"Popup interceptado: {popup.url}")
            
            with popup.expect_download(timeout=15000) as download_info:
                pass
                
            download = download_info.value
            file_path = os.path.join(output_dir, download.suggested_filename)
            download.save_as(file_path)
            log.bind(etapa="estrategia").info(f"Arquivo salvo: {file_path}")
            return file_path
        except TimeoutError:
            log.bind(etapa="estrategia").warning("Nenhum popup detectado.")
            return None

    @staticmethod
    def fetch_via_repository(page: Page, output_dir: str) -> Optional[str]:
        """
        Estratégia 3: Captura via Repositório de Arquivos (MSAppFileView).
        """
        log.bind(etapa="estrategia").info("Iniciando resgate via Repositório (MSAppFileView)...")
        page.locator('button:has-text("Imprimir")').last.click()
        page.wait_for_timeout(5000) 
        
        page.keyboard.press('Escape')
        page.wait_for_timeout(1000)
        page.keyboard.press('Escape')
        
        try:
            page.locator('text="Repositório"').first.click()
            page.wait_for_timeout(3000)
            
            primeiro_arquivo = page.locator('table tr').nth(1)
            primeiro_arquivo.click()
            
            with page.expect_download(timeout=15000) as download_info:
                page.locator('button:has-text("Transferir")').click()
                
            download = download_info.value
            file_path = os.path.join(output_dir, download.suggested_filename)
            download.save_as(file_path)
            log.bind(etapa="estrategia").info(f"Sucesso! Arquivo recuperado: {file_path}")
            return file_path
            
        except Exception as e:
            log.bind(etapa="estrategia").error(f"Falha ao resgatar do Repositório: {e}")
            return None

    @staticmethod
    def fallback_html_scraping(page: Page, output_csv_path: str) -> bool:
        """
        Estratégia 4: Fallback para HTML Scraping.
        """
        log.bind(etapa="estrategia").info("Executando Fallback: HTML Scraping via DOM...")
        try:
            selects = page.locator("select").all()
            formato_alterado = False
            for sel in selects:
                if sel.is_visible():
                    options = sel.locator("option").all()
                    textos = [opt.inner_text().strip() for opt in options]
                    if "HTML" in textos:
                        sel.select_option(label="HTML")
                        log.bind(etapa="estrategia").info("Formato alterado para HTML.")
                        formato_alterado = True
                        break
            
            if not formato_alterado:
                log.bind(etapa="estrategia").warning("Não foi possível alterar para HTML.")
                return False

            page.locator('button:has-text("Imprimir")').last.click()
            page.wait_for_timeout(5000)
            
            linhas = page.locator("table tr").all()
            
            if not linhas:
                log.bind(etapa="estrategia").warning("Nenhuma tabela HTML localizada.")
                return False
                
            dados_csv = []
            for row in linhas:
                colunas = row.locator("td, th").all()
                texto_colunas = [col.inner_text().strip() for col in colunas]
                dados_csv.append(texto_colunas)
                
            with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerows(dados_csv)
                
            log.bind(etapa="estrategia").info(f"Sucesso! Extraídas {len(dados_csv)} linhas para {output_csv_path}")
            return True
        except Exception as e:
            log.bind(etapa="estrategia").error(f"Erro no Scraping: {e}")
            return False
