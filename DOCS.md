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

### Sprint 6: Sessão e Resiliência de Longo Prazo
*   **Gestão de Sessão**: Implementamos detecção automática de logout e re-login. O robô agora monitora a URL e a presença de elementos de login para garantir que execuções longas não expirem.
*   **Recuperação de Erros**: Refinamos o sistema de `Esc` e navegação de retorno para que o robô consiga se "limpar" após um erro e continuar para o próximo técnico sem intervenção humana.

### Sprint 7: Validação e Polish
*   **Refinamento do Matching**: Ajuste de thresholds de imagem para lidar com variações sutis de renderização no Canvas.
*   **Limpeza de Código**: Centralização de configurações e logs para facilitar a manutenção futura.

### Sprint 8: Integração com Pipeline de Dados (Produção)
*   **Arquitetura Centralizada**: Migramos a saída de dados para `~/Documents/projects/data_pipeline/robo_totvs/entrada/`. O robô agora entrega arquivos diretamente onde o time de BI consome.
*   **UUID e Unicidade**: Alteramos a nomenclatura de arquivos para UUID v4. Isso remove a dependência de nomes de técnicos no sistema de arquivos e garante que cada arquivo gerado seja único, evitando colisões em re-processamentos.
*   **Estrutura Temporal**: Implementamos a criação dinâmica de subpastas datadas dentro do pipeline para facilitar a orquestração do processamento de dados por dia.

### Sprint 8.1 (Hotfix): Limite de Conexões do Protheus e Falha-Silenciosa
**Incidente observado em produção (2026-05-10).** A execução `python main.py --incluir-desligados --reset` processou com sucesso os 19 primeiros técnicos da lista, mas abortou logo após. O resumo final marcou "14 falhas" — **incluindo CT e DJ (os armazéns LOGISTICA VVA e LOGISTICA ACZ)**, técnicos que estavam no fim da lista. A investigação dos screenshots de evidência revelou o real motivo: o Protheus injetou um modal HTML com o texto:

> **Help: HELP — Problema: Limite de conexoes do Usuario excedido**

Causa raiz: o servidor mantém um teto de sessões simultâneas por usuário. Como o robô fechava o navegador sem fazer logout explícito, cada execução deixava uma sessão fantasma no servidor; ao acumular várias rodadas no mesmo dia, batemos no teto. A partir desse modal, qualquer navegação travava — o template matching da Home falhava (score 0.092 vs threshold 0.65) porque a tela estava coberta pelo diálogo.

**Bug secundário descoberto**: `flows/processar_lista.py` contabilizava os técnicos não-tentados como falha quando o loop abortava por irrecuperabilidade (`falha += (total - idx)`). Isso mascarava o motivo real (problema sistêmico) atrás de "14 falhas individuais" e forçava o operador a usar `--retry-falhos` para retomar, o que reprocessa também eventuais falhas verdadeiras.

**Correção implementada:**

1.  **Detecção dedicada** (`core/acoes.py::detectar_limite_conexoes`): polling em DOM (todos os frames) por `text=/Limite de conex[oõ]es/i` e por heurística `Help` + botão `Fechar`. Não exige template matching — o modal é HTML acessível.
2.  **Tentativa de recuperação suave** (`fechar_modal_limite_conexoes`): clica `Fechar` via DOM, espera, re-verifica. Se o modal sumiu e a tela ficou navegável, tenta `fazer_login` + `navegar_ate_rotina` e continua o loop.
3.  **Aborto duro quando irrecuperável** (`SessaoEsgotadaError`): se o modal persiste após `Fechar` ou se o re-login é rejeitado, levanta exceção dedicada que sobe até `main.py` e termina com exit 2. A mensagem instrui o operador a pedir ao admin do Protheus para encerrar as sessões fantasma (Monitor de Conexões / APSDU). Não fazemos retry cego — a causa é externa e exige ação humana.
4.  **Checkpoint honesto**: ao abortar por `SessaoEsgotadaError`, os técnicos não-tentados ficam como `pendente` no checkpoint, não como `falhou`. Re-execução simples (`python main.py`, sem `--reset` nem `--retry-falhos`) retoma exatamente nos pendentes.
5.  **Detecção proativa por iteração**: além da detecção dentro de `_preparar_para_proximo`, o loop principal checa o modal **antes** de cada técnico. Assim, se o modal aparecer mid-download, o robô o pega na próxima iteração em vez de gastar 3 retries cegos primeiro.

**Resultado:** 0 falsos positivos de "falha" em técnicos não-tentados; mensagem de erro que aponta diretamente a ação corretiva; retomada idempotente sem flags adicionais. Documentado em PRD §6.5.1 (F5.1), §13.13 (decisão de aborto duro) e §13.14 (pendente vs falho).

**Aberto para próxima sprint:** logout explícito ao final de toda execução (clique no menu do usuário → "Sair") para eliminar sessões fantasma na origem. Hoje o cleanup depende do timeout do servidor (~horas).

### Sprint 9: Integração Timezone e Pipeline com dmais_portal (2026-05-22)
*   **Correção de timezone no consumidor**: Identificado que `import_stock` (dmais_portal) usava `timezone.now().date()` (UTC) para `snapshot_date`, criando snapshots com data errada entre 00h–03h UTC (21h–00h BRT). Corrigido para `datetime.today().date()` (data local BRT).
*   **Fallback de diretório no pipeline**: `consolidar_estoque.py` agora usa `_encontrar_diretorio_entrada()` — tenta hoje → ontem → mais recente disponível. Resolve problema quando robô falha ou ainda não rodou no dia.
*   **Propagação de data dos dados**: A data do diretório encontrado (`data_date`) é repassada de `consolidar_estoque.py` → `executar_consolidacao` → `import_stock` → `StockSnapshot.snapshot_date`, garantindo que o dashboard mostre a data real dos dados consolidados.
*   **Views timezone-safe**: `EstoqueIndexView` agora usa `timezone.localdate()` (Django TIME_ZONE) em vez de `date.today()` (SO) para cálculo do `consolidation_status`.

## 3. Principais Decisões Técnicas

| Desafio | Solução Adotada | Por quê? |
| :--- | :--- | :--- |
| **Localização de Elementos** | Híbrido DOM + Template Matching | O DOM do Protheus é instável e ofuscado; o Canvas é inacessível por texto. |
| **Lentidão do Protheus** | Polling de Imagem + Retry Exponencial | Evita o uso de `sleep()` fixos e torna o robô mais rápido em dias que o sistema está bom. |
| **Identificação do IFrame** | Busca por Input Name | IDs de Iframes mudam a cada sessão; procurar pelo nome do campo de input é infalível. |
| **Validação de Sucesso** | OCR + ZIP Check | Garantir que o conteúdo do download corresponde ao técnico filtrado. |
| **Integração de Dados** | Saída UUID em Dir Central | Facilita a ingestão por pipelines de BI e remove caminhos relativos frágeis. |
| **Limite de conexões do Protheus (modal HELP)** | Detecção DOM dedicada + aborto duro com exit 2 | Re-login imediato falharia (servidor ainda enxerga as sessões antigas); melhor sinalizar a causa ao operador do que mascarar com retries cegos. |
| **Técnicos não-tentados** | Marcados como `pendente`, não como `falhou` | Preserva semântica do checkpoint e habilita retomada idempotente sem `--retry-falhos`. |

## 4. Estado Atual
O robô é capaz de:
1.  Fazer login de forma resiliente.
2.  Navegar até a rotina de estoque.
3.  Processar uma lista inteira de técnicos com persistência de estado.
4.  Recuperar de falhas individuais e de sessão.
5.  Detectar e tratar o limite de conexões do Protheus, abortando limpo e com mensagem acionável quando irrecuperável (Sprint 8.1).
6.  Entregar arquivos prontos para produção em estrutura de pipeline de dados.
7.  Gerar evidências visuais (screenshots) de cada erro para depuração.

---
*Documento atualizado em: 22 de Maio de 2026*
