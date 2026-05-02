# Documentação de Evolução do Projeto — robo-totvs

Este documento registra a jornada técnica, os desafios enfrentados e as soluções implementadas para automatizar o download de relatórios no TOTVS Protheus WebApp.

## 1. O Desafio Inicial: A "Parede de Canvas"
O Protheus WebApp utiliza o SmartClient HTML, que renderiza a interface quase inteiramente dentro de elementos `<canvas>`. Isso torna o DOM (Document Object Model) "opaco" para ferramentas de automação tradicionais como o Playwright puro ou Selenium, pois os botões, campos e menus não são elementos HTML individuais, mas sim pixels pintados.

### Solução: Estratégia Híbrida
Decidimos por uma abordagem em três camadas:
1.  **DOM-First**: Sempre que um elemento é acessível via HTML (como a tela de login inicial), usamos Locators do Playwright por serem mais rápidos e estáveis.
2.  **Computer Vision (OpenCV)**: Para interagir com o que está "dentro" do Canvas, usamos o Template Matching. O robô tira um print da tela e procura por padrões visuais (armazenados em `referencias/`).
3.  **OCR (Tesseract)**: Para validar dados variáveis (como nomes de técnicos) que só aparecem renderizados no Canvas.

## 2. Linha do Tempo de Desenvolvimento

### Sprint 1: Fundação (Setup & Bootstrap)
*   **Estabilização da Viewport**: Fixamos a resolução em **1366x768**. Isso foi crucial para que os prints de referência fossem compatíveis entre diferentes máquinas.
*   **Infraestrutura**: Configuração de logs estruturados (Loguru), variáveis de ambiente (Pydantic Settings) e inicialização do Playwright.

### Sprint 2: A Barreira do Login
*   **Descoberta dos IFrames**: O Protheus carrega o formulário de login dentro de um IFrame dinâmico. Desenvolvemos o `_frame_login` para localizar recursivamente onde os campos `login` e `password` estão escondidos.
*   **Resiliência**: Implementação de retries com a biblioteca `tenacity`, permitindo que o robô recupere de lentidões do sistema.

### Sprint 3: Navegação nos Favoritos
*   **Menu de Favoritos**: Implementamos a navegação da Home até a rotina "Mat. Estoque por Técnico".
*   **Popups Condicionais**: O sistema apresenta popups aleatórios (como o "Não exibir nos próximos 7 dias"). Criamos funções de "probe" que detectam e fecham essas janelas sem interromper o fluxo principal.

### Sprint 4: O "Santo Graal" (Download de 1 Técnico)
*   **O Problema do Dropdown**: O campo "Tipo de Planilha" era particularmente difícil de clicar via imagem. Descobrimos que podíamos usar o `select_option` do Playwright diretamente no elemento `<select>` escondido no DOM do IFrame, o que trouxe 100% de estabilidade para este passo.
*   **Captura de Downloads**: Utilizamos o evento `expect_download` do Playwright para interceptar o arquivo gerado pelo servidor, renomeando-o seguindo a convenção `{code}_{name}.xlsx`.
*   **Integridade de Dados**: Adicionamos cálculo de hash SHA-256 e validação de arquivo ZIP para garantir que o download foi concluído com sucesso e sem corrupção.

### Sprint 5: Processamento em Lote e Idempotência
*   **Checkpoint System**: Criamos um sistema de persistência em JSON (`state/checkpoint.json`). Se o robô cair no meio de uma lista de 100 técnicos, ele sabe exatamente de onde parou ao ser reiniciado.
*   **Validação de Lista**: Implementamos o filtro por `status == "Ativo"` e a carga dinâmica do `technicians.json`.

## 3. Principais Decisões Técnicas

| Desafio | Solução Adotada | Por quê? |
| :--- | :--- | :--- |
| **Localização de Elementos** | Híbrido DOM + Template Matching | O DOM do Protheus é instável e ofuscado; o Canvas é inacessível por texto. |
| **Lentidão do Protheus** | Polling de Imagem + Retry Exponencial | Evita o uso de `sleep()` fixos e torna o robô mais rápido em dias que o sistema está bom. |
| **Identificação do IFrame** | Busca por Input Name | IDs de Iframes mudam a cada sessão; procurar pelo nome do campo de input é infalível. |
| **Validação de Sucesso** | OCR + ZIP Check | Garantir que o conteúdo do download corresponde ao técnico filtrado. |

## 4. Estado Atual
O robô é capaz de:
1.  Fazer login de forma resiliente.
2.  Navegar até a rotina de estoque.
3.  Processar uma lista inteira de técnicos.
4.  Recuperar de falhas individuais sem abortar a execução total.
5.  Gerar evidências visuais (screenshots) de cada erro para depuração.

---
*Documento atualizado em: 02 de Maio de 2026*
