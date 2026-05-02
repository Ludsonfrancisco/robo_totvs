import os
import csv
from typing import Optional
from playwright.sync_api import Page, TimeoutError

class ProtheusDownloadStrategies:
    """
    Classe contendo estratégias para contornar o bloqueio de download (Ambiente "Servidor") 
    no TOTVS Protheus WebApp.
    """

    @staticmethod
    def intercept_network_binary(page: Page, output_path: str) -> bool:
        """
        Estratégia 1: Injeção/Interceptação de Rede (Network Interception).
        Verifica requisições de rede (XHR/Fetch) disparadas pelo Protheus que possam 
        conter o binário do arquivo, interceptando-as antes que sejam descartadas.
        """
        print("[Strategy] Iniciando monitoramento de rede (Network Interception)...")
        download_success = False

        def handle_response(response):
            nonlocal download_success
            content_type = response.header_value('content-type') or ''
            url_lower = response.url.lower()
            
            # Verifica se o Content-Type é Excel ou se a URL sugere um endpoint de download
            if 'spreadsheetml' in content_type or 'ms-excel' in content_type or 'download' in url_lower:
                try:
                    body = response.body()
                    if len(body) > 0:
                        with open(output_path, 'wb') as file:
                            file.write(body)
                        download_success = True
                        print(f"  -> [Network Intercept] Sucesso! Binário capturado da URL: {response.url}")
                except Exception as e:
                    print(f"  -> [Network Intercept] Falha ao ler binário da rede: {e}")

        # Anexa o listener de rede
        page.on('response', handle_response)
        
        try:
            # Dispara o clique no botão Imprimir (ajuste o seletor conforme o DOM real)
            page.locator('button:has-text("Imprimir")').last.click()
            # Aguarda tempo suficiente para a requisição ir ao servidor e voltar
            page.wait_for_timeout(10000)
        finally:
            # Remove o listener para não afetar o restante da automação
            page.remove_listener('response', handle_response)

        return download_success

    @staticmethod
    def monitor_hidden_popup(page: Page, output_dir: str) -> Optional[str]:
        """
        Estratégia 2: Monitoramento de Pop-ups Ocultos.
        Verifica se o Protheus tenta abrir uma nova Window/Aba (que o navegador pode estar 
        bloqueando nativamente), onde o link ou evento de download pode estar encapsulado.
        """
        print("[Strategy] Monitorando popups ocultos (Hidden Pop-ups)...")
        try:
            with page.expect_popup(timeout=8000) as popup_info:
                page.locator('button:has-text("Imprimir")').last.click()
            
            popup = popup_info.value
            popup.wait_for_load_state('domcontentloaded')
            print(f"  -> [Popup Monitor] Popup interceptado na URL: {popup.url}")
            
            # Em alguns casos do Protheus Web, o popup em si aciona o download
            with popup.expect_download(timeout=15000) as download_info:
                # O download pode começar sozinho ao carregar, ou exigir um clique em um iframe
                pass
                
            download = download_info.value
            file_path = os.path.join(output_dir, download.suggested_filename)
            download.save_as(file_path)
            print(f"  -> [Popup Monitor] Arquivo salvo em: {file_path}")
            return file_path
            
        except TimeoutError:
            print("  -> [Popup Monitor] Nenhum popup detectado após o clique em Imprimir.")
            return None

    @staticmethod
    def fetch_via_repository(page: Page, output_dir: str) -> Optional[str]:
        """
        Estratégia 3: Captura via Repositório de Arquivos (MSAppFileView).
        Se o sistema trava o download para 'Servidor', o robô confirma a geração do arquivo, 
        navega até o repositório nativo do Protheus e o busca.
        """
        print("[Strategy] Iniciando fluxo de resgate via Repositório (MSAppFileView)...")
        # 1. Confirma a geração no Servidor
        page.locator('button:has-text("Imprimir")').last.click()
        page.wait_for_timeout(5000) 
        
        # 2. Retorna para o menu principal/Home
        page.keyboard.press('Escape')
        page.wait_for_timeout(1000)
        page.keyboard.press('Escape')
        
        # 3. Acessa a rotina do Repositório (ajuste o caminho/seletor da sua árvore de menus)
        try:
            # Exemplo de clique na rotina de Repositório (pode variar de acordo com o Módulo/Menu)
            page.locator('text="Repositório"').first.click()
            page.wait_for_timeout(3000)
            
            # 4. Seleciona o arquivo gerado (geralmente o primeiro da lista, ordenado por data)
            # Adaptar o localizador para a tabela específica da MSAppFileView
            primeiro_arquivo = page.locator('table tr').nth(1)
            primeiro_arquivo.click()
            
            # 5. Captura o download nativamente do repositório
            with page.expect_download(timeout=15000) as download_info:
                page.locator('button:has-text("Transferir")').click() # ou "Baixar", "Download"
                
            download = download_info.value
            file_path = os.path.join(output_dir, download.suggested_filename)
            download.save_as(file_path)
            print(f"  -> [Repository] Arquivo recuperado com sucesso para: {file_path}")
            return file_path
            
        except Exception as e:
            print(f"  -> [Repository] Falha ao navegar ou resgatar o arquivo no Repositório: {e}")
            return None

    @staticmethod
    def fallback_html_scraping(page: Page, output_csv_path: str) -> bool:
        """
        Estratégia 4: Fallback para HTML Scraping.
        Se tudo falhar e o XLS for inacessível, o robô altera o tipo do relatório para HTML, 
        renderiza na tela e extrai os dados via DOM Scraping diretamente para um CSV.
        """
        print("[Strategy] Executando Fallback: HTML Scraping via DOM...")
        try:
            # 1. Identificar o select e mudar o tipo para HTML
            selects = page.locator("select").all()
            formato_alterado = False
            for sel in selects:
                if sel.is_visible():
                    options = sel.locator("option").all()
                    textos = [opt.inner_text().strip() for opt in options]
                    if "HTML" in textos:
                        sel.select_option(label="HTML")
                        print("  -> [Scraping] Formato do relatório alterado para HTML.")
                        formato_alterado = True
                        break
            
            if not formato_alterado:
                print("  -> [Scraping] Não foi possível alterar para formato HTML.")
                return False

            # 2. Imprimir/Gerar Relatório em Tela
            page.locator('button:has-text("Imprimir")').last.click()
            page.wait_for_timeout(5000) # Aguarda renderização
            
            # 3. Raspar os dados da tabela gerada (Muitas vezes em um iframe ou modal)
            # O selector table tr deve ser ajustado ao layout que o Protheus cospe em HTML
            linhas = page.locator("table tr").all()
            
            if not linhas:
                print("  -> [Scraping] Nenhuma tabela HTML localizada no DOM.")
                return False
                
            dados_csv = []
            for row in linhas:
                colunas = row.locator("td, th").all()
                texto_colunas = [col.inner_text().strip() for col in colunas]
                dados_csv.append(texto_colunas)
                
            # 4. Salvar em disco
            with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerows(dados_csv)
                
            print(f"  -> [Scraping] Sucesso! Extraídas {len(dados_csv)} linhas para {output_csv_path}")
            return True
            
        except Exception as e:
            print(f"  -> [Scraping] Erro durante a raspagem do DOM: {e}")
            return False
