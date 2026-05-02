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
- [ ] **Schema pydantic do `technicians.json`** com campos `code` (obrigatório), `name`, `login`, `status`, `email` (todos opcionais) — PRD §4.2
- [ ] **Filtro padrão `status == "Ativo"`** ao carregar a lista; flag `--incluir-desligados` para override
- [ ] `core/estado.py` com `salvar_checkpoint()` / `carregar_checkpoint()` — escrita atômica (write-to-temp + rename, PRD §13.5)
- [ ] Checkpoint grava `code`, `status`, `tentativas`, `arquivo`, `hash_sha256`, `erro_msg`
- [ ] `flows/processar_lista.py` orquestrador completo — itera, atualiza checkpoint após cada técnico
- [ ] Flag `--retry-falhos` (reprocessa apenas `status=falhou` do checkpoint atual)
- [ ] Exit codes corretos (0 todos ok / 1 falhas parciais / 3 erro de config)
- [ ] Demo: lista real de técnicos `Ativo` do `technicians.json` processada; re-execução pula sucessos


### Sprint 6 — Resiliência: Sessão e Erros
**Objetivo:** sobrevive a logout, timeouts e estados inesperados.
- [ ] Detecção de logout via template match da tela de login (referências `03`/`04`/`05`)
- [ ] Re-login automático com retomada do técnico atual
- [ ] **Contador global de re-logins por execução; ao atingir 3 → aborta com exit code 2** (PRD F5 — proteção contra loop infinito)
- [ ] Screenshot automático em toda falha → `logs/evidencias/<timestamp>_<etapa>.png`
- [ ] Validação pré-técnico: confirmar via screenshot que está na home/favoritos antes de iniciar F3
- [ ] Resumo final colorido no console (verde sucesso / amarelo warning / vermelho erro)
- [ ] Demo: forçar logout no meio da execução; robô recupera sozinho. Forçar 3 logouts seguidos; robô aborta com exit 2.

### Sprint 7 — (Opcional) MCP Context7
**Objetivo:** classificador de tela quando matching falha.
- [ ] `core/contexto.py::classificar_tela()` (stub primeiro)
- [ ] Integração real com servidor MCP Context7
- [ ] Acionado apenas após N falhas de matching
- [ ] Decisão go/no-go ao final da Sprint 6 com base em métricas reais

### Sprint 8 — Refinamento
**Objetivo:** pronto para produção diária.
- [ ] Rotação de logs (10MB / 5 arquivos)
- [ ] README completo (setup, troubleshooting, FAQ)
- [ ] Script de instalação (`install.sh`)
- [ ] Ajuste de thresholds de matching com base em histórico
- [ ] Limpeza de TODOs e dead code

---