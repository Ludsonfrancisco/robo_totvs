## 12. Roadmap de Sprints

> Cada sprint é incremental. **Não avançar** sem checklist anterior 100% completo. Sprint termina com demo manual em ambiente real.

### Sprint 1 — Setup do Projeto + Bootstrap do Navegador
**Objetivo:** projeto Python rodando, Playwright abrindo Protheus, screenshot funciona.
- [x] Criar `requirements.txt` com versões mínimas (PRD §3.1)
- [x] Criar venv e instalar dependências (`playwright install chromium`)
- [x] Estrutura de pastas (`core/`, `flows/`, `logs/`, `state/`, `downloads/`) — `technicians.json` já está na raiz
- [x] **Normalizar `referencias/01_link_de_acesso` → `01_link_de_acesso.png`** (arquivo está sem extensão no repo)
- [x] `core/config.py` com `pydantic-settings` lendo `.env` — cobrir as 8 vars do PRD §9.1 (`PROTHEUS_URL`, `PROTHEUS_USER`, `PROTHEUS_PASS`, `HEADLESS`, `VIEWPORT_W`, `VIEWPORT_H`, `DOWNLOAD_TIMEOUT_S`, `TECNICOS_JSON` com default `technicians.json`)
- [x] `core/log.py` com loguru configurado (arquivo + console, rotação 10MB/5 arquivos)
- [x] `core/navegador.py` com `iniciar_navegador()` + `tirar_screenshot()` — viewport fixa 1366×768
- [x] `main.py` que abre Protheus, espera 5s, fecha — apenas valida boot
- [x] `.env.example` com as 8 vars (sem secrets) + `.gitignore` (downloads/, logs/, state/, .env)
- [x] README.md mínimo com setup

### Sprint 2 — Login Resiliente
**Objetivo:** robô faz login com sucesso e detecta falhas.
- [x] `core/visao.py` com `aguardar_imagem(referencia, timeout)` e `clicar_imagem(referencia)`
- [x] `core/acoes.py::fazer_login()` cobrindo passos 01–07 das referências
- [x] Tratamento de popup inicial (passo 02) condicional
- [x] Retry com tenacity (3 tentativas, backoff exponencial)
- [x] Detecção de credencial inválida → exit code 2
- [x] Senha mascarada em todos os logs
- [x] Demo: login completo até home em ambiente real

### Sprint 3 — Navegação até a Rotina
**Objetivo:** robô chega à tela de filtro de técnico.
- [x] `acoes.py::navegar_ate_rotina()` cobrindo passos 07–11
- [x] Tratamento condicional do popup "7 dias" (passo 10)
- [x] Validação de estado entre cada clique
- [x] Demo: da home até campo de código preenchível

### Sprint 4 — Download de 1 Técnico (hardcoded)
**Objetivo:** baixar 1 XLSX ponta-a-ponta para um código fixo do `technicians.json`.
- [x] `acoes.py::baixar_xlsx_tecnico(code, name)` cobrindo passos 11–18
- [x] Captura do evento `download` do Playwright (timeout 60s configurável via `DOWNLOAD_TIMEOUT_S`)
- [x] Renomeação para `{code}_{name_norm}.xlsx` em `downloads/AAAA-MM-DD/`
- [x] Validação: arquivo > 0 bytes + abre como zip (`zipfile.is_zipfile`)
- [x] **Cálculo do hash SHA-256** do arquivo baixado (gravado no checkpoint na Sprint 5)
- [x] **Validação OCR opcional do `name`** (PRD F3 passo 3): se o JSON traz `name`, OCR na região do nome retornado + comparação fuzzy via `rapidfuzz`. Mismatch → log warning, não bloqueia.
- [x] Espera retorno automático à home (passo 18, sleep 7s — exceção documentada em PRD §13.8)
- [x] Recovery: se sistema não retornar à home em 15s → forçar via Esc/Voltar
- [x] Demo: 1 XLSX baixado, validado, organizado em `downloads/AAAA-MM-DD/`

### Sprint 5 — Loop pela Lista JSON + Checkpoint
**Objetivo:** processar lista com idempotência.
- [x] **Schema pydantic do `technicians.json`** com campos `code` (obrigatório), `name`, `login`, `status`, `email` (todos opcionais) — PRD §4.2
- [x] **Filtro padrão `status == "Ativo"`** ao carregar a lista; flag `--incluir-desligados` para override
- [x] `core/estado.py` com `salvar_checkpoint()` / `carregar_checkpoint()` — escrita atômica (write-to-temp + rename, PRD §13.5)
- [x] Checkpoint grava `code`, `status`, `tentativas`, `arquivo`, `hash_sha256`, `erro_msg`
- [x] `flows/processar_lista.py` orquestrador completo — itera, atualiza checkpoint após cada técnico
- [x] Flag `--retry-falhos` (reprocessa apenas `status=falhou` do checkpoint atual)
- [x] Exit codes corretos (0 todos ok / 1 falhas parciais / 3 erro de config)
- [x] Demo: lista real de técnicos `Ativo` do `technicians.json` processada; re-execução pula sucessos


### Sprint 6 — Resiliência: Sessão e Erros
**Objetivo:** sobrevive a logout, timeouts e estados inesperados.
- [x] Detecção de logout via template match da tela de login (referências `03`/`04`/`05`)
- [x] Re-login automático com retomada do técnico atual
- [x] **Contador global de re-logins por execução; ao atingir 3 → aborta com exit code 2** (PRD F5 — proteção contra loop infinito)
- [x] Screenshot automático em toda falha → `logs/evidencias/<timestamp>_<etapa>.png`
- [x] Validação pré-técnico: confirmar via screenshot que está na home/favoritos antes de iniciar F3
- [x] Resumo final colorido no console (verde sucesso / amarelo warning / vermelho erro)
- [x] Demo: forçar logout no meio da execução; robô recupera sozinho. Forçar 3 logouts seguidos; robô aborta com exit 2.

### Sprint 7 — (Opcional) MCP Context7
**Objetivo:** classificador de tela quando matching falha.
- [x] `core/contexto.py::classificar_tela()` (stub primeiro)
- [x] Integração real com servidor MCP Context7 *(Cancelado: No-Go baseado em métricas)*
- [x] Acionado apenas após N falhas de matching *(Cancelado: No-Go baseado em métricas)*
- [x] Decisão go/no-go ao final da Sprint 6 com base em métricas reais *(Decisão: NO-GO, 0% de falhas por "tela desconhecida" nos logs)*

### Sprint 8 — Refinamento
**Objetivo:** pronto para produção diária.
- [x] Rotação de logs (10MB / 5 arquivos)
- [x] README completo (setup, troubleshooting, FAQ)
- [x] Script de instalação (`install.sh`)
- [x] Ajuste de thresholds de matching com base em histórico
- [x] Limpeza de TODOs e dead code

---

## F7 — Transferência Múltipla baseada em Planilha (Sprints 9-12)

> Implementa PRD §6.7. Reaproveita F1 (login) e F2 (com novo parâmetro de rotina); **não substitui** F1–F6. Cada sprint termina com demo manual contra Protheus real e screenshot de evidência preservado em `logs/evidencias/trans_mult/`.

### Sprint 9 — Planilha: leitura, schema e validação pré-execução
**Objetivo:** dado um caminho de XLSX, retornar `list[LinhaTransferencia]` validado ou abortar com exit 3 e mensagem citando linha/coluna ofensora — **sem abrir o navegador**. Sprint não toca Protheus; testável com fixtures de XLSX.
- [x] Adicionar `openpyxl>=3.1.5` em `requirements.txt` (PRD §13.11)
- [x] Criar `referencias/trans_mult.xlsx` com cabeçalho real (20 colunas da PRD §6.7.1) — arquivo atual está com 0 bytes; preencher com 1 linha de exemplo válida + commit
- [x] Estender `core/config.py` com `transferencia_xlsx_path: Path = Path("referencias/trans_mult.xlsx")` e `TRANSFERENCIA_XLSX` em `.env.example`
- [x] Estender `core/schema.py` com modelo `LinhaTransferencia` (Pydantic) — 20 campos da PRD §6.7.1; `Decimal` (não `float`) para `quantidade` e `potencia`; `validade` como `date` opcional formato `dd/mm/aaaa`; `numero_serie` obrigatório com `min_length=1`
- [x] Estender `core/schema.py` com modelo `CheckpointTransferenciaMultipla` (campos da PRD §6.7.4)
- [x] Estender `core/schema.py` com dataclass/modelo `PlanilhaCarregada(linhas: list[LinhaTransferencia], sha256: str, caminho: Path)` — retorno unificado de `carregar_transferencias`
- [x] Criar `core/planilha.py` com `carregar_transferencias(path: Path) -> PlanilhaCarregada`
- [x] Em `core/planilha.py`: validar `zipfile.is_zipfile(path)` antes de abrir (mesma checagem de F3) — falha ⇒ `PlanilhaInvalidaError`
- [x] Em `core/planilha.py`: normalizar nomes de cabeçalho da primeira aba — case-insensitive, remover espaços/pontos/underscores (`Prod.Orig.` ≡ `prod orig` ≡ `PROD_ORIG`)
- [ ] Em `core/planilha.py`: rejeitar planilha se faltar qualquer coluna obrigatória (✅ em PRD §6.7.1) — erro lista as colunas faltantes
- [ ] Em `core/planilha.py`: para cada linha de dado, validar que `prod_orig`, `armazem_orig`, `prod_destino`, `armazem_destino`, `numero_serie`, `quantidade` são não-vazios; `quantidade` parseável como `Decimal` — erro cita linha (1-indexed, descontando cabeçalho) e coluna
- [ ] Em `core/planilha.py`: calcular SHA-256 do arquivo bruto e popular `PlanilhaCarregada.sha256` — usado pela idempotência da Sprint 12
- [ ] Adicionar exceção `PlanilhaInvalidaError(Exception)` em `core/acoes.py` (mesma família de `LoginError`/`NavegacaoError`); `main.py` mapeia para exit 3
- [ ] **Demo:** rodar `core/planilha.py` (script ad-hoc ou via REPL) contra 3 fixtures: (a) planilha boa de 5 linhas — retorna 5 modelos válidos; (b) planilha com `numero_serie` vazio na linha 3 — `PlanilhaInvalidaError` cita "linha 3, coluna numero_serie"; (c) planilha com `quantidade="abc"` na linha 2 — erro cita "linha 2, coluna quantidade não-decimal".

### Sprint 10 — Navegação F2 multi-rotina + abrir Inclusão + capturar Numero Documento
**Objetivo:** robô loga, navega até `Tranf. Multipla`, abre o formulário de inclusão e lê o `Numero Documento` autogerado, persistindo-o no checkpoint **antes** de tocar no grid. Testável com planilha vazia (não preenche linhas).
- [ ] Refatorar `acoes.py::navegar_ate_rotina()` para aceitar `rotina: Literal["mat_estoque", "trans_multipla"] = "mat_estoque"` — default mantém comportamento atual
- [ ] Mapear referências por rotina: `mat_estoque` → `08_clicar_Mat_Estoque_Por_Tecnico.png`; `trans_multipla` → `08.1_Tranf._Multipla.png` (PRD §10.3)
- [ ] Após selecionar `trans_multipla`, fluxo segue **diferente** do `mat_estoque`: não há diálogo "Confirmar" nem popup "7 dias" — `navegar_ate_rotina` deve ramificar e parar quando a tela `Transferencia Mod. II` (com botão `+ Incluir`) estiver visível [ref09.1]
- [ ] Criar `acoes.py::abrir_inclusao_trans_multipla(page)` — clica `+ Incluir` via template matching em `09.1_Incluir.png`; valida via OCR do cabeçalho que o título mudou para `Transferencia Mod. II - INCLUIR` antes de retornar; envolto em `@retry(stop_after_attempt(3), wait=wait_exponential(...))`
- [ ] Criar `acoes.py::capturar_numero_documento(page) -> str` — preferência DOM (`input` adjacente ao label "Numero Documento", varrendo `[page, *page.frames]`); fallback OCR em região fixa relativa ao cabeçalho [ref11.1]; resultado precisa casar regex `^[A-Z0-9]{10,15}$` (formato observado: `YUXI000005MX1`) — caso contrário levanta `NavegacaoError`
- [ ] Em `flows/transferencia_multipla.py` (esqueleto inicial): após capturar o número, gravar **imediatamente** em `state/transferencia_multipla_AAAA-MM-DD.json` via `core/estado.py` (escrita atômica) com `status="em_andamento"`, `numero_documento`, `iniciada_em`, `planilha_sha256` — antes de qualquer interação com o grid (PRD §6.7.2 passo 2)
- [ ] Logging: `log.bind(etapa="trans_mult.abrir", documento=numero).info(...)` — campo `tecnico` recebe `"-"` neste fluxo (PRD §6.7.6)
- [ ] **Demo:** `python main.py trans-multipla --planilha <fixture_vazia.xlsx>` ⇒ robô loga, navega até `Tranf. Multipla`, abre INCLUIR, captura o número (ex.: `YUXI000005MX1`), grava no checkpoint, sai com exit 0 sem preencher nada. Verificar arquivo de checkpoint contém o número correto. Validar manualmente no Protheus que **nenhum documento foi salvo** (já que não houve clique em Salvar) Headless=False.

### Sprint 11 — Preencher uma linha do grid + retry por linha + evidências
**Objetivo:** dado um `LinhaTransferencia`, preencher os 20 campos do grid na ordem correta com `Quantidade` por último, recuperar de erros por retry de linha (3×) e abortar a execução inteira após esgotar. Testável com planilha de 1 linha.
- [ ] Criar `acoes.py::preencher_linha_grid(page, linha: LinhaTransferencia)` — ordem fixa de campos da PRD §6.7.2 passo 4; `Tab` entre campos; **`quantidade` por último, encerrado com `Enter`** (PRD §13.12)
- [ ] Pular campos opcionais vazios (não digitar nada, **não** pressionar Tab vazio — preservar o foco na próxima coluna obrigatória)
- [ ] Após digitar `prod_orig` + Tab: aguardar até 3s pelo auto-preenchimento de `desc_orig`/`um_orig`. Se a planilha trouxe valor explícito divergente do auto-preenchido, sobrescrever; se nada vier em 3s, log warning e prosseguir (PRD §6.7.3)
- [ ] Detectar popup de erro do Protheus após qualquer campo: comparar screenshot com referência genérica de modal de erro (capturar uma e adicionar como `referencias/19.1_popup_erro_protheus.png` quando aparecer pela 1ª vez no demo) ou OCR do cabeçalho de modal — se detectado, fechar via `Esc`/clicar X, levantar `NavegacaoError`
- [ ] Decorar `preencher_linha_grid` com `@retry(stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))` — antes de cada retry, limpar a linha com `Esc` ou `Ctrl+A`+`Del` para reiniciar do estado limpo
- [ ] Após esgotar 3 tentativas: criar exceção `TransferenciaIncompletaError(numero_documento, linha_index)` em `acoes.py` e re-levantar; orquestrador (Sprint 12) mapeia para exit 1
- [ ] Em qualquer falha (incluindo entre retries): `tirar_screenshot(page, etapa=f"trans_mult.linha{N}")` salvando em `logs/evidencias/trans_mult/<timestamp>_linha<N>.png` — criar a subpasta na primeira chamada
- [ ] Logging por linha: ao **entrar** em `preencher_linha_grid` ⇒ `log.bind(etapa="trans_mult.linha", linha=N, documento=numero).info("preenchendo")`; ao concluir com sucesso ⇒ `.info("ok")`; em falha após retries ⇒ `.error(erro_msg)`
- [ ] **Demo (parte 1 — happy path):** preparar planilha de 1 linha com produto/armazém válidos no ambiente Protheus. Rodar end-to-end (sem chamar Salvar — apenas até o `Enter` da quantidade). Verificar no Protheus que o cursor pulou para a linha 2 do grid. Cancelar a tela.
- [ ] **Demo (parte 2 — falha esperada):** preparar planilha de 1 linha com `prod_orig` inexistente. Rodar; observar 3 tentativas com popup do Protheus, cada uma gerando screenshot em `logs/evidencias/trans_mult/`. Robô aborta com `TransferenciaIncompletaError`, exit 1. Validar manualmente que o documento INCLUIR foi descartado (não persistiu no Protheus).

### Sprint 12 — Orquestrador end-to-end: loop + Salvar + checkpoint + idempotência + CLI
**Objetivo:** comando único `python main.py trans-multipla` lê planilha de N linhas, processa todas, salva o documento no Protheus, atualiza o checkpoint final e respeita idempotência por `planilha_sha256`. Testável com planilha real de 5+ linhas.
- [ ] Criar `acoes.py::salvar_documento_trans_multipla(page)` — clica `Salvar` via `12.1_clicar_salvar.png` (PRD §10.3); aguarda até 30s por uma das condições: (a) volta ao grid `Transferencia Mod. II` com nova linha, OR (b) popup de sucesso, OR (c) modal de erro
- [ ] Em caso de modal de erro pós-`Salvar`: OCR do texto do modal, log `error`, screenshot em `logs/evidencias/trans_mult/<ts>_salvar.png`, levantar `TransferenciaIncompletaError(numero_documento, linha_index=-1)` ⇒ exit 1
- [ ] Implementar `flows/transferencia_multipla.py` orquestrador completo (substituindo o esqueleto da Sprint 10):
  1. `carregar_transferencias(planilha_path)` ⇒ `(linhas, sha256)`; falha ⇒ exit 3 antes de abrir navegador
  2. **Idempotência:** ler `state/transferencia_multipla_AAAA-MM-DD.json` do dia. Se existir com `planilha_sha256` igual e `status="sucesso"` ⇒ logar e sair com exit 0 sem abrir navegador (PRD §6.7.5 último critério)
  3. F1 (login) → F2 (`rotina="trans_multipla"`) → `abrir_inclusao_trans_multipla` → `capturar_numero_documento` → grava checkpoint inicial (Sprint 10 já cobre)
  4. Foco na 1ª célula do grid via clique em `10.1_loop_de_materail_baseado_na_planilha.png`
  5. Loop por `linhas`: para cada `linha_n`, chamar `preencher_linha_grid(page, linha_n)`; após retorno bem-sucedido, pressionar seta `↓` (PRD §6.7.2 — navegação entre linhas é por seta, **não por clique**); validar via screenshot que cursor está na coluna `Prod.Orig.` da linha N+1
  6. Após última linha confirmada: `salvar_documento_trans_multipla(page)`
  7. Atualizar checkpoint final: `status="sucesso"`, `linhas_total=N`, `linhas_ok=N`, `salvo_em=<ts ISO>`
- [ ] Tratamento de sessão expirada durante o loop (integrar com F5): re-login + **abortar a F7 inteira com exit 1** (não retomar — o documento INCLUIR foi descartado pelo Protheus, PRD §6.7.3 última linha). Checkpoint mantém `numero_documento` antigo como órfão para auditoria
- [ ] Estender `main.py` com subcomando `trans-multipla [--planilha <path>]` via `argparse`; comando default sem argumento continua disparando `flows/processar_lista.py` (F1–F6) — não regredir
- [ ] Adicionar exit codes mapeados ao novo fluxo: `PlanilhaInvalidaError` ⇒ 3, `TransferenciaIncompletaError` ⇒ 1, `CredenciaisInvalidasError` ⇒ 2 (já existente), sucesso ⇒ 0
- [ ] Resumo final no terminal estilo F1–F6: cabeçalho `robo-totvs — Transferência Múltipla`, progresso `[N/Total]` por linha, e bloco final `RESUMO documento: YUXI...  linhas: N/N  duração: MM:SS`
- [ ] Atualizar `CLAUDE.md` (seção "Setup & commands") com o novo subcomando — apenas linha de exemplo; **não** descrever arquitetura que já vive no PRD
- [ ] **Demo (happy path):** planilha real de 5 linhas válidas no ambiente Protheus. Rodar `python main.py trans-multipla`. Validar no Protheus que documento `<numero>` existe com 5 linhas no grid. Conferir checkpoint do dia tem `status=sucesso`, `linhas_ok=5`, `numero_documento` correto, `planilha_sha256` populado.
- [ ] **Demo (idempotência):** rodar o **mesmo comando, mesma planilha** 1 minuto depois. Robô deve sair com exit 0 sem abrir navegador, logando "planilha já processada hoje (sha256 igual)". Confirmar no Protheus que continua existindo só **um** documento.
- [ ] **Demo (recuperação de sessão):** preparar planilha de 5 linhas; durante o preenchimento da linha 3 (operador derruba a sessão manualmente no Protheus). Robô detecta logout, aborta com exit 1, screenshot salvo, checkpoint marca o `numero_documento` como órfão. Validar manualmente no Protheus que **nenhum** documento parcial foi salvo.

---