# PRD — Robô TOTVS Protheus (Download "Mat. Estoque por Técnico")

> **Documento de requisitos do produto (PRD)**
> Fonte única de verdade. Toda decisão técnica, sprint ou prompt subsequente deve referenciar este arquivo.
> **Versão:** 1.9 — 2026-05-22
> **Autor:** Ludson Francisco
> **Status:** Pronto para Produção (Sprints 1-8 Concluídas + hotfix 8.1 "Limite de Conexões") · F7 (Transferência Múltipla) em planejamento

---

## 1. Visão Geral do Produto

**Nome:** robo-totvs (RPA Protheus — Estoque por Técnico)

**Resumo (1 frase):** Robô em Python + Playwright que automatiza, no TOTVS Protheus WebApp, o download em XLSX do relatório "Material em Estoque por Técnico" para uma lista de técnicos definida em JSON, integrando-os a um pipeline de dados profissional.

**Status Atual:**
O projeto evoluiu do MVP para uma arquitetura de pipeline de dados. Os downloads não são mais locais ao projeto, mas enviados para uma estrutura centralizada no diretório do usuário, utilizando nomes únicos (UUID) para garantir integridade no processamento downstream.

**Conquistas Técnicas Recentes:**
1.  **Arquitetura de Pipeline**: Migração dos downloads da pasta interna para `~/Documents/projects/data_pipeline/robo_totvs/entrada/`.
2.  **Nomenclatura UUID**: Cada download recebe nome `UUID_tecnico.xlsx` para evitar colisões.
3.  **Organização Temporal**: Criação automática de subpastas por data de execução (`AAAA-MM-DD`) dentro do pipeline.
4.  **Consumidor downstream (dmais_portal)**: O portal D+ consome os `.xlsx` via volume Docker compartilhado. `consolidar_estoque.py` (no dmais_portal) usa fallback hierárquico: tenta `entrada/<HOJE>`, depois `entrada/<ONTEM>`, depois o mais recente disponível. A data do diretório encontrado é repassada para `import_stock` como `snapshot_date` (data local BRT, não UTC), garantindo que o card "Última Consolidação" do dashboard reflita a data real dos dados.
5.  **Estabilização Visual**: Implementação de viewport fixa (1366x768) e multi-scale matching.
6.  **Idempotência**: Sistema de checkpoint em JSON que permite retomar execuções.

**Problema que resolve:**
A operação atual é 100% manual: o operador faz login no Protheus, navega até Favoritos → "Mat Estoque Por Tecnico", insere o código de cada técnico, escolhe o formato XLSX, clica em "Imprimir" e baixa o arquivo. Para uma lista com N técnicos, são ~10 cliques por item × N + erros humanos + tempo ocioso esperando o sistema responder. O Protheus WebApp roda em SmartClient HTML com uso intensivo de Canvas, IDs ofuscados e iFrames — o que dificulta automações tradicionais baseadas em DOM.

**Objetivo do MVP:**
Rodar todos os dias (ou sob demanda) um job que:
1. Abra o Protheus, faça login.
2. Navegue até a rotina-alvo.
3. Para cada técnico do JSON de entrada, baixe o XLSX.
4. Volte ao ponto inicial e processe o próximo, até o fim.
5. Gere logs estruturados e organize os arquivos baixados.

**Não-objetivos (fora do escopo do MVP):**
- ❌ Containerização (Docker) — pode entrar em fase 2.
- ❌ Testes automatizados (unit/integration) — fase 2.
- ❌ Interface gráfica de configuração — operação por CLI/JSON.
- ❌ Distribuir relatórios por email/Slack — fase 2.
- ❌ Multiusuário ou multi-tenant — apenas 1 conta Protheus.
- ❌ Modificar dados no Protheus (write) — operação é **read-only / export-only**.

**Frequência esperada de execução:** diária (manual ou via cron), ~30–200 técnicos por execução.

---

## 2. Público-Alvo

| Persona | Papel | Necessidade | Como interage com o robô |
|---|---|---|---|
| **Operador de Logística / Almoxarifado** | Quem hoje faz o download manual | Reduzir 2–4h/dia de trabalho repetitivo | Edita `data/tecnicos.json`, executa `python main.py`, recebe XLSX organizados em `downloads/AAAA-MM-DD/` |
| **Analista de Dados / BI** | Consome os XLSX gerados | Receber arquivos em horário previsível e nomenclatura consistente | Lê arquivos baixados; não interage diretamente com o robô |
| **Desenvolvedor (mantenedor)** | Mantém o robô | Diagnóstico rápido quando o Protheus muda layout | Lê `logs/`, ajusta seletores/imagens em `core/visao/` |

**Restrições do usuário-alvo (Operador):**
- Não é desenvolvedor: precisa rodar com 1 comando.
- Conhece os códigos dos técnicos.
- Pode editar JSON simples.

---

## 3. Arquitetura do Sistema

### 3.1 Stack escolhida

| Camada | Tecnologia | Versão mínima | Justificativa |
|---|---|---|---|
| Linguagem | Python | 3.11+ | Maturidade, ecossistema RPA, async nativo |
| Automação navegador | **Playwright (Python)** | `playwright>=1.49.0` | Suporte robusto a iFrames, espera inteligente (`wait_for_state`), interceptação de download, cross-browser, headed/headless |
| Computer Vision (fallback) | OpenCV + template matching | `opencv-python>=4.10.0` | Para Canvas / elementos não-DOM; matching com prints de `referencias/` |
| OCR (último recurso) | Tesseract via `pytesseract` | `pytesseract>=0.3.13` | Quando matching falha e precisamos ler texto de Canvas |
| Logging | `loguru` | `loguru>=0.7.2` | Estruturado, colorido, rotação de arquivos com 1 linha de config |
| Config | `python-dotenv` + `pydantic-settings` | `pydantic-settings>=2.6.0` | Validação de env vars (URL, USER, PASS) com tipo |
| Retry | `tenacity` | `tenacity>=9.0.0` | Decorators de retry com backoff exponencial |
| Captura de tela (debug) | Playwright nativo | — | Evidência de erro em cada falha |

> **Regra de versionamento (IA):** ao gerar código, sempre use as versões mínimas acima. Não invente bibliotecas fora desta lista sem justificar.

### 3.2 Diagrama de arquitetura (Mermaid)

```mermaid
flowchart TB
    subgraph Entry["Entrada"]
        JSON["data/tecnicos.json"]
        ENV[".env (URL, USER, PASS)"]
    end

    subgraph App["main.py"]
        ORCH["flows/processar_lista.py<br/>(orquestrador)"]
    end

    subgraph Core["core/"]
        NAV["navegador.py<br/>(Playwright)"]
        ACO["acoes.py<br/>(login, navegar, baixar)"]
        VIS["visao.py<br/>(OpenCV + OCR)"]
        CTX["contexto.py<br/>(MCP Context7 — opcional)"]
        EST["estado.py<br/>(checkpoint do progresso)"]
    end

    subgraph Saida["Saída"]
        DL["~/Documents/projects/data_pipeline/.../entrada/AAAA-MM-DD/"]
        LOG["logs/run-AAAA-MM-DD.log"]
        EVID["logs/evidencias/*.png"]
    end

    subgraph Externo["Sistema externo"]
        TOTVS["TOTVS Protheus<br/>WebApp (SmartClient HTML)"]
    end

    JSON --> ORCH
    ENV --> ORCH
    ORCH --> ACO
    ACO --> NAV
    ACO --> VIS
    ACO -.-> CTX
    ACO --> EST
    NAV <--> TOTVS
    ACO --> DL
    ORCH --> LOG
    ACO --> EVID
```

### 3.3 Princípios arquiteturais

1. **Estratégia híbrida em 3 camadas, sempre nesta ordem:**
   - **DOM (Playwright locators)** → preferência absoluta quando elemento é HTML acessível (login form, header geral).
   - **Computer Vision (template matching)** → quando elemento está em Canvas (a maior parte do Protheus): clique por coordenada após `match` em screenshot.
   - **OCR** → último recurso para ler valores variáveis dentro de Canvas (ex.: validar nome do técnico que apareceu).

2. **Espera inteligente, nunca `sleep` cego:**
   - Para DOM: `page.wait_for_selector(..., state="visible")`.
   - Para Canvas: polling de template matching com timeout (`aguardar_imagem(path, timeout=15)`).
   - `sleep` fixo só é permitido para janelas conhecidamente longas e estáveis (ex.: post-download de 7s observado no fluxo manual — passo 18).

3. **Retry com backoff por etapa, não por execução inteira:**
   - Cada ação atômica (clicar, digitar, baixar) tem 3 tentativas com `tenacity`.
   - Se etapa falha após retry → captura screenshot, registra erro, marca técnico como "falhou", continua próximo.

4. **Idempotência por técnico:**
   - Estado salvo em `state/checkpoint.json` após cada técnico processado.
   - Re-execução pula técnicos já baixados no mesmo dia (ou conforme flag `--retry-falhos`).

5. **Sessão como cidadão de primeira classe:**
   - Detecção de logout (URL ou screenshot da tela de login) em todo retry.
   - Re-login automático e retomada do técnico atual.

6. **Não há multi-tenancy nem usuários múltiplos** — 1 robô, 1 conta Protheus.

---

## 4. Modelo de Dados

Não há banco de dados. Os dados ficam em arquivos JSON simples.

### 4.1 Diagrama (entidades lógicas)

```mermaid
erDiagram
    EXECUCAO ||--o{ TECNICO_PROCESSADO : "processa"
    TECNICO_PROCESSADO ||--o| DOWNLOAD : "gera"
    TECNICO_PROCESSADO ||--o{ EVIDENCIA_ERRO : "registra"

    EXECUCAO {
        string id_execucao "UUID + timestamp"
        datetime iniciada_em
        datetime finalizada_em
        int total_tecnicos
        int sucesso
        int falha
        string status "em_andamento | concluida | abortada"
    }

    TECNICO_PROCESSADO {
        string codigo "ex: 000123"
        string nome "opcional, p/ validação"
        string status "pendente | sucesso | falhou"
        int tentativas
        string erro_msg "se falhou"
        datetime processado_em
    }

    DOWNLOAD {
        string caminho_arquivo
        int tamanho_bytes
        string hash_sha256
        datetime baixado_em
    }

    EVIDENCIA_ERRO {
        string caminho_screenshot
        string etapa "login | navegar | baixar | etc"
        string mensagem
    }
```

### 4.2 Entrada — `technicians.json` (raiz do projeto)

> **Artefato real já disponível no repositório:** `technicians.json` (na raiz, não em `data/`).
> O campo usado pelo robô como entrada do filtro Protheus é **`code`** (ex.: `"HK"`, `"8Q"`, `"2S"`).
> Os campos `name`, `login`, `status` e `email` são metadados; `name` é usado pela validação OCR opcional (passo F3.3) e `status` pode ser usado para filtrar apenas `Ativo`.

```json
[
  { "code": "HK", "name": "ALEXANDRE MENEZES DE SOUZA - DMAIS (VAREJO)", "login": "ALEXANDRE MENEZES DE SOUZA - DMAIS (VAREJO)", "status": "Ativo", "email": "alexandre.souza@dmais.com.br" },
  { "code": "8Q", "name": "CLAUDIO OLIVEIRA DE MORAIS - DMAIS (VAREJO)", "login": "CLAUDIO OLIVEIRA DE MORAIS - DMAIS (VAREJO)", "status": "Ativo", "email": "claudio.morais@dmais.com.br" },
  { "code": "DJ", "name": "LOGISTICA VVA", "login": "", "status": "Ativo", "email": "" }
]
```

| Campo | Tipo | Obrigatório | Observação |
|---|---|---|---|
| `code` | string | ✅ | Código do técnico no Protheus (formato livre, 2 chars alfanuméricos no dataset atual; tratado como string) |
| `name` | string | ❌ | Se presente, robô valida via OCR que o nome retornado pelo Protheus contém este texto — defesa contra código errado |
| `login` | string | ❌ | Metadado; não usado pelo fluxo de download |
| `status` | string | ❌ | `Ativo` \| `Desligado` — robô deve, por padrão, processar apenas `Ativo` (configurável) |
| `email` | string | ❌ | Metadado; reservado para fase 2 (envio por email) |

> **Compatibilidade com o PRD original:** referências ao caminho `data/tecnicos.json` e aos campos `codigo`/`nome` em outras seções deste documento devem ser lidas como **`technicians.json`** com `code`/`name`. O env var `TECNICOS_JSON` (seção 9.1) tem default atualizado para `technicians.json`.

### 4.3 Saída — checkpoint `state/checkpoint.json`

```json
{
  "id_execucao": "2026-05-01T08:30:00_a1b2c3",
  "iniciada_em": "2026-05-01T08:30:00",
  "finalizada_em": null,
  "tecnicos": [
    { "codigo": "000123", "status": "sucesso", "tentativas": 1, "arquivo": "downloads/2026-05-01/000123_JOAO_DA_SILVA.xlsx" },
    { "codigo": "000456", "status": "falhou", "tentativas": 3, "erro_msg": "timeout aguardando popup de download" },
    { "codigo": "000789", "status": "pendente" }
  ]
}
```

### 4.4 Saída — arquivos baixados

- Diretório: `~/Documents/projects/data_pipeline/robo_totvs/entrada/AAAA-MM-DD/` (cria automaticamente).
- Convenção de nome: `{uuid4}.xlsx` (ex.: `e5ee3ba2-b1d2-4f13-8cfe-ffd1c2690257.xlsx`).
- Validação pós-download: arquivo existe, `> 0 bytes`, extensão `.xlsx`, abre como ZIP válido (XLSX é ZIP).

---

## 5. Apps / Módulos

Estrutura de pastas (Python, sem framework web):

```
robo-totvs/
├── core/
│   ├── __init__.py
│   ├── navegador.py        # boot Playwright, contexto, downloads, screenshots
│   ├── acoes.py            # ações de alto nível (login, navegar_favorito, selecionar_tecnico, baixar_xlsx)
│   ├── visao.py            # template matching (OpenCV) + OCR (Tesseract)
│   ├── contexto.py         # integração opcional com MCP Context7
│   ├── estado.py           # leitura/escrita do checkpoint.json
│   ├── config.py           # pydantic-settings: env vars + paths
│   └── log.py              # configuração loguru centralizada
│
├── flows/
│   └── processar_lista.py  # orquestrador: lê JSON, chama ações, atualiza estado
│
├── data/
│   └── tecnicos.json       # entrada (lista de técnicos)
│
├── referencias/            # prints com setas — usados para template matching
│   ├── 01_link_de_acesso(.png)
│   ├── 02_clicar_ok.png
│   ├── ... 18_aguarde_7seg_e_voltara_para_o_passo_07.png
│
├── downloads/              # XLSX baixados (criado em runtime, gitignored)
│   └── AAAA-MM-DD/
│
├── state/                  # checkpoint.json (gitignored)
│
├── logs/                   # logs estruturados + screenshots de erro (gitignored)
│   ├── run-AAAA-MM-DD.log
│   └── evidencias/
│
├── .env.example            # URL, USER, PASS — sem secrets
├── .gitignore
├── requirements.txt
├── README.md
└── main.py                 # ponto de entrada CLI
```

---

## 6. Funcionalidades Detalhadas

### 6.1 F1 — Inicialização e Login Resiliente

**Descrição:** Abrir Protheus, dispensar popups iniciais e autenticar com credenciais de `.env`.

**Fluxo (mapeado às referências):**
1. Abre URL (ref. `01_link_de_acesso`).
2. Aguarda carregamento do SmartClient (loader some / aparece campo de login).
3. Se aparecer popup inicial → clica "OK" (ref. `02_clicar_ok.png`) — via template matching.
4. Insere usuário (ref. `03_insira_usuario.png`).
5. Insere senha (ref. `04_insira_senha.png`).
6. Clica "Entrar" (ref. `05_clicar_entrar.png`).
7. Clica "Entrar" novamente na tela de confirmação/empresa (ref. `06_clicar_entrar.png`).
8. Aguarda home (ref. `07_pagina_home_clicar_favoritos.png` carregada).

**Regras:**
- Senha **nunca** logada em texto.
- Falha de login após 3 tentativas → aborta execução com exit code 2.
- Detecção: se URL contém marker de login após uma ação de navegação → considera sessão expirada e re-autentica.

**Critérios de aceite:**
- [ ] Login com credenciais válidas chega na home em < 30s.
- [ ] Credenciais inválidas detectadas e reportadas em log estruturado.
- [ ] Popup inicial dispensado quando presente; ausência do popup não quebra o fluxo.
- [ ] Senha não aparece em nenhum log.

---

### 6.2 F2 — Navegação até a Rotina

**Descrição:** A partir da home, navegar até a rotina "Mat Estoque Por Técnico" via Favoritos.

**Fluxo:**
1. Na home, clica "Favoritos" (ref. `07_pagina_home_clicar_favoritos.png`).
2. Clica "Mat Estoque Por Tecnico" (ref. `08_clicar_Mat_Estoque_Por_Tecnico.png`).
3. Clica "Confirmar" no diálogo que aparece (ref. `09_clicar_confirmar.png`).
4. **Se aparecer** popup "Não exibir próximos 7 dias" (ref. `10`) → clica nele para dispensar.
5. Aguarda tela do filtro de técnico (ref. `11_colocar_o_codigo_tecnico.png`) carregar.

**Regras:**
- Passo 4 é **opcional/condicional** — implementar como "tentar matching com timeout curto (3s); se não achar, segue".
- Cada clique deve ser seguido de validação de estado (próxima imagem aparece).

**Critérios de aceite:**
- [ ] Navegação completa em < 20s da home até o campo de código do técnico.
- [ ] Popup "7 dias" dispensado quando aparece; ausência não quebra fluxo.
- [ ] Falha de navegação dispara screenshot + retry da etapa.

---

### 6.3 F3 — Seleção do Técnico e Download XLSX

**Descrição:** Para 1 técnico, preencher código, escolher tipo "Planilha" + formato XLSX, baixar arquivo.

**Fluxo:**
1. Insere código do técnico no campo (ref. `11_colocar_o_codigo_tecnico.png`).
2. Clica "OK" para confirmar filtro (ref. `12_clicar_OK.png`).
3. **Validação opcional (defensiva):** se o JSON traz `nome`, fazer OCR na região e validar. Se não bater → log warning, continua.
4. Clica "Planilha" (ref. `13_clicar_planilha.png`).
5. Clica no dropdown "Tipo de planilha" (ref. `14_clicar_tipo_de_planilha.png`).
6. Seleciona "Formato de Tabela XLSX" (ref. `15_clicar_Formato_de_Tabela_xlsx.png`).
7. Clica "Imprimir" (ref. `16_clicar_Imprimir.png`).
8. Clica "Sim" no popup de confirmação de geração (ref. `17_clique_Sim.png`).
9. Espera evento `download` do Playwright **ou** novo arquivo aparecer no dir de downloads (timeout 60s).
10. Renomeia para `{codigo}_{nome_norm}.xlsx` em `downloads/AAAA-MM-DD/`.
11. Valida: tamanho > 0, abre como zip.
12. Aguarda ~7s e o sistema retorna ao passo 7 / Favoritos automaticamente (ref. `18_aguarde_7seg_e_voltara_para_o_passo_07.png`).

**Regras:**
- Se o sistema **não** retornar à home/favoritos sozinho após 15s → forçar via "Esc"/"Voltar" ou ir direto para passo 7.
- Se download não chegar em 60s → marca como falha; **não** tenta re-imprimir no mesmo iframe (pode duplicar); volta à home e reinicia para o mesmo técnico (até `tentativas=3`).
- Hash SHA-256 do arquivo gravado no checkpoint para auditoria.

**Critérios de aceite:**
- [ ] 1 técnico baixado com sucesso ponta-a-ponta.
- [ ] Arquivo final tem nome correto, > 0 bytes, abre como XLSX.
- [ ] Falha em qualquer passo gera screenshot em `logs/evidencias/`.
- [ ] Após download bem-sucedido, robô está pronto para o próximo técnico (na tela de Favoritos).

---

### 6.4 F4 — Loop pela Lista JSON

**Descrição:** Iterar `data/tecnicos.json`, processar todos, tolerando falhas individuais.

**Regras:**
- Lê JSON validando schema (`pydantic` model).
- Para cada técnico:
  - Se já está no checkpoint com `status=sucesso` na execução atual → pula (idempotente).
  - Caso contrário, executa F3.
  - Atualiza checkpoint após cada técnico (não em lote — sobrevive a crashes).
- Ao final: gera resumo (sucesso/falha/total) em log e exit code 0 (todos ok) ou 1 (alguma falha).

**Critérios de aceite:**
- [ ] Lista de 5 técnicos processada com 100% de sucesso em ambiente estável.
- [ ] Falha em técnico N não impede processamento de N+1.
- [ ] Re-execução com checkpoint do dia pula técnicos já baixados.
- [ ] Flag `--retry-falhos` reprocessa apenas os com `status=falhou`.

---

### 6.5 F5 — Detecção e Recuperação de Sessão

**Descrição:** Robustez contra logout / timeout do Protheus.

**Mecanismo:**
- Antes de cada técnico, valida via screenshot que está na home/favoritos.
- Se detecta tela de login (template match) → re-executa F1 → retoma técnico atual.
- Limite de 3 re-logins por execução (proteção contra loop infinito).

**Critérios de aceite:**
- [ ] Forçar logout no meio da execução → robô detecta, faz login, segue.
- [ ] 3 logouts seguidos sem progresso → aborta execução com erro claro.

---

### 6.5.1 F5.1 — Tratamento do modal "Limite de Conexões do Usuário Excedido"

> **Status:** novo escopo (v1.8) — hotfix da Sprint 8.1. Sub-feature de F5 (recuperação de sessão).

**Descrição:** O Protheus mantém um teto de sessões simultâneas por usuário (licença/concorrência). Quando o robô bate nesse teto — tipicamente por sessões fantasma de execuções anteriores que ainda não expiraram no servidor — o ERP injeta um modal HTML bloqueante:

> **Help: HELP**
> **Problema: Limite de conexoes do Usuario excedido**
> *[Fechar]*

Sem tratamento explícito, o robô interpretava o modal como "tela desconhecida": o template matching falhava em Favoritos (score 0.092 vs threshold 0.65), o `_preparar_para_proximo` retornava `False`, e o orquestrador marcava **todos os técnicos restantes como falha silenciosa** (incidente real em 2026-05-10: 14 técnicos não-tentados — incluindo armazéns CT e DJ no fim da lista — contabilizados como "falha"; ver `DOCS.md` Sprint 8.1).

**Fluxo de tratamento:**

1. **Detecção** (`core/acoes.py::detectar_limite_conexoes`): a cada iteração do loop e dentro de `_preparar_para_proximo`, verifica via DOM em todos os frames:
   - Texto regex `/Limite de conex[oõ]es/i`.
   - Heurística secundária: cabeçalho `/Help.*HELP/i` + botão `Fechar` visíveis no mesmo frame.
2. **Tentativa de fechamento** (`fechar_modal_limite_conexoes`): clica `button:has-text("Fechar")` via DOM (fallback `Enter`).
3. **Re-verificação**: após `Fechar`, se o modal voltar a aparecer (servidor continua recusando), o robô levanta `SessaoEsgotadaError` — **não** insiste em re-logar (re-login imediato falharia com a mesma rejeição).
4. **Re-login condicional**: se o modal sumiu, executa `fazer_login` + `navegar_ate_rotina`. Falha de re-login (`CredenciaisInvalidasError`/`NavegacaoError`) é convertida em `SessaoEsgotadaError`.
5. **Aborto limpo**: `SessaoEsgotadaError` sobe até `main.py` → **exit code 2** com mensagem instruindo o operador a pedir ao admin do Protheus para encerrar as sessões fantasma (Monitor de Conexões / APSDU) e rodar novamente. **Sem `--reset`**, o checkpoint preserva os técnicos pendentes e a re-execução retoma de onde parou.

**Contraste com F5:**

| Cenário | F5 (logout normal) | F5.1 (limite de conexões) |
|---|---|---|
| Sintoma na tela | Tela de login visível | Modal HELP + botão Fechar |
| Causa | Timeout server-side da sessão | Teto de sessões simultâneas por usuário |
| Recuperação automática | Re-login no mesmo run | **Não** — exige ação humana (admin) |
| Estado dos técnicos restantes | Continuam após re-login | Permanecem `pendente` no checkpoint |
| Exit code | 0 ou 1 | **2** |

**Regra crítica de contagem (anti-falha-silenciosa):**

Antes deste hotfix, ao abortar o loop por irrecuperabilidade, `processar_lista` fazia `falha += (total - idx)` — inflando o número de falhas com técnicos jamais tentados. Após v1.8, quando o aborto é por `SessaoEsgotadaError`, os técnicos não-tentados **não** são contabilizados como falha; permanecem `pendente` no checkpoint. Isso preserva a semântica de "falha" (tentativa que não baixou XLSX) e habilita retomada idempotente sem `--retry-falhos`.

**Critérios de aceite:**

- [ ] Modal "Limite de conexões" injetado mid-loop é detectado em < 1 ciclo e fechado via DOM.
- [ ] Modal persistente após `Fechar` ⇒ aborto com `SessaoEsgotadaError` → exit 2 e mensagem citando ação do admin.
- [ ] Técnicos restantes após aborto por F5.1 ficam `pendente` no checkpoint (não `falhou`).
- [ ] Re-execução sem `--reset` retoma exatamente nos técnicos pendentes.
- [ ] Resumo final reflete apenas as tentativas reais — não conta não-tentados como falha.

---

### 6.6 F6 — Logging e Evidências

**Descrição:** Rastreabilidade completa da execução.

**Conteúdo:**
- 1 log por execução: `logs/run-AAAA-MM-DD-HHMMSS.log`.
- Formato: timestamp ISO | nível | etapa | técnico | mensagem.
- Em **toda** falha: screenshot em `logs/evidencias/<timestamp>_<etapa>.png`.
- Resumo final: total, sucesso, falha, duração média por técnico.

**Critérios de aceite:**
- [ ] Log de uma execução real é legível e permite reproduzir o problema.
- [ ] Senha nunca aparece em log.
- [ ] Tamanho de log gerenciável (rotação a 10MB / 5 arquivos).

---

### 6.7 F7 — Transferência Múltipla baseada em Planilha

> **Status:** novo escopo (v1.7) — entra como Sprint 9. Reaproveita F1 (login), F2 (navegação até rotina, com referências `*.1`) e a infraestrutura de visão/log/checkpoint. **Não substitui** F3–F6, que continuam servindo o pipeline de download "Mat. Estoque por Técnico".

**Descrição:** A partir de uma planilha XLSX de entrada (`referencias/trans_mult.xlsx` ou caminho configurável via env), criar **um único documento de Transferência Múltipla** no Protheus (rotina `Tranf. Multipla` do módulo Estoque/Custos), preenchendo uma linha do grid para cada linha da planilha, e ao final salvar o documento — capturando o `Numero Documento` autogerado pelo Protheus e armazenando-o no checkpoint para auditoria.

Diferente do fluxo F3 (export-only / read-only sobre o Protheus), **esta feature escreve dados no ERP**. Erro silencioso aqui produz dado incorreto em produção, então as garantias de validação por linha e o checkpoint pós-salvamento são parte do contrato funcional, não otimizações.

**Pré-requisitos:**
- F1 concluído (sessão autenticada).
- F2 navega até **`Tranf. Multipla`** (não `Mat Estoque Por Técnico`) — usa as referências da família `*.1` (ver §10.3).
- Planilha de entrada existe, é XLSX válido, tem ao menos 1 linha de dados além do cabeçalho.

#### 6.7.1 Estrutura da planilha de entrada

> **Fonte:** `referencias/trans_mult.xlsx` (artefato existente no repo). As colunas espelham 1-para-1 os campos do grid `Transferencia Mod. II — INCLUIR` (ver `referencias/10.1_loop_de_materail_baseado_na_planilha.png`). Nomes de coluna são lidos pelo cabeçalho da primeira aba — **case-insensitive**, espaços e pontos opcionais (ex.: `Prod.Orig.` / `prod orig` / `PROD_ORIG` são equivalentes).

| Coluna | Campo no grid | Obrigatória | Observação |
|---|---|---|---|
| `prod_orig` | Prod.Orig. | ✅ | Código do produto de origem |
| `desc_orig` | Desc.Orig. | ❌ | Auto-preenchida pelo Protheus após `prod_orig`; ler da planilha apenas se diferir |
| `um_orig` | UM Orig. | ❌ | Idem (auto) |
| `armazem_orig` | Armazem Orig. | ✅ | Código do armazém de origem |
| `endereco_orig` | Endereco Orig. | ❌ | |
| `prod_destino` | Prod.Destino | ✅ | Código do produto de destino |
| `desc_destino` | Desc.Destino | ❌ | Auto |
| `um_destino` | UM Destino | ❌ | Auto |
| `armazem_destino` | Armazem Destino | ✅ | |
| `endereco_destino` | Endereco Destino | ❌ | |
| `numero_serie` | Numero Serie | ✅ | **Regra de negócio: obrigatório em toda linha** — linha sem `numero_serie` é rejeitada antes de tocar o Protheus |
| `lote` | Lote | ❌ | |
| `sub_lote` | Sub-Lote | ❌ | |
| `validade` | Validade | ❌ | Formato `dd/mm/aaaa` quando presente |
| `potencia` | Potencia | ❌ | Numérico |
| `quantidade` | Quantidade | ✅ | **Trigger funcional: ao preencher Quantidade, o Protheus avança para a próxima linha do grid** (regra observada em UI). Por isso, é o **último** campo escrito da linha. |
| `qt_2aum` | Qt 2aUM | ❌ | |
| `estornado` | Estornado | ❌ | |
| `sequencia` | Sequencia | ❌ | |
| `lote_destino` | Lote Destino | ❌ | |

**Validação pré-execução** (executada uma vez antes de abrir o Protheus, em `flows/transferencia_multipla.py`):
- Arquivo existe e abre como XLSX válido (mesma checagem de `zipfile.is_zipfile` usada em F3).
- Cabeçalho contém todas as colunas marcadas ✅ acima.
- Toda linha de dado tem `prod_orig`, `armazem_orig`, `prod_destino`, `armazem_destino`, `numero_serie` e `quantidade` não-vazios.
- `quantidade` parseável como decimal (`Decimal`, não `float`, para evitar erro de arredondamento em quantidades fracionárias).

Falha de validação ⇒ aborta com **exit code 3** (config/dado inválido) **antes de abrir o navegador**. Mensagem de erro lista linha(s) e coluna(s) ofensoras.

#### 6.7.2 Fluxo detalhado

> Notação: `[refX.Y]` = referência visual em `referencias/X.Y_*.png`. Toda interação respeita o contrato híbrido DOM → CV → OCR de §3.3.

```
Pré-condições já garantidas: F1 (login) e F2 (Favoritos → "Tranf. Multipla" via [ref08.1])
```

1. **Abrir formulário de inclusão.** Na tela `Transferencia Mod. II` [ref09.1], clicar em `+ Incluir`. Aguardar até o título mudar para `Transferencia Mod. II - INCLUIR` (validação por OCR no cabeçalho).
2. **Capturar `Numero Documento`.** O Protheus autogera um código no campo `Numero Documento` (ex.: `YUXI000005MX1`) [ref11.1]. Ler o valor via DOM (preferência: `<input>` com label adjacente "Numero Documento"); se for canvas, OCR sobre região fixa relativa ao cabeçalho. **Persistir imediatamente** no checkpoint da execução — se o robô morrer no meio do preenchimento, o operador precisa do código para fechar/cancelar manualmente o documento órfão.
3. **Posicionar foco na primeira célula do grid.** Clicar na célula `Prod.Orig.` da primeira linha [ref10.1].
4. **Loop por linha da planilha** — para cada `linha_planilha` (1..N):
   1. **Preencher campos na ordem** abaixo, sempre confirmando cada campo com `Tab` (avança coluna):
      - `prod_orig` → Tab → aguardar auto-preenchimento de `desc_orig`/`um_orig` (até 3s; se a planilha trouxe valor explícito divergente, sobrescrever).
      - `armazem_orig` → Tab.
      - `endereco_orig` (se presente) → Tab.
      - `prod_destino` → Tab → aguardar auto-preenchimento equivalente do destino.
      - `armazem_destino` → Tab.
      - `endereco_destino` (se presente) → Tab.
      - `numero_serie` → Tab. **(obrigatório — sem este campo, a linha não pode ser confirmada)**
      - `lote`, `sub_lote`, `validade`, `potencia` (se presentes) → Tab cada.
      - **`quantidade` por último** → Enter (ou Tab final). Esta é a confirmação da linha — antes desta tecla, **nenhuma linha deve ser dada como concluída**.
   2. **Avançar para próxima linha** com seta `↓` (regra de negócio: navegação entre linhas é por seta, não por clique). Validar via screenshot que o cursor está na coluna `Prod.Orig.` da linha N+1 e que a linha N tem todos os campos visíveis.
   3. **Logar** sucesso da linha N com `etapa="trans_mult.linha"`, `linha=N`, `numero_documento=<capturado no passo 2>`.
5. **Salvar o documento.** Após a última linha confirmada, clicar em `Salvar` [ref12.1]. Aguardar (até 30s) feedback de sucesso (mudança de tela / popup de confirmação / volta ao grid de Tranf. Multipla com nova linha).
6. **Validação pós-salvamento.** Se o Protheus retornar erro modal ("Falha ao salvar", "Saldo insuficiente", etc.) — capturar texto via OCR, screenshot da evidência, marcar **execução inteira como falha**, não como falha-parcial. Justificativa: um documento de transferência ou salva inteiro ou não salva — não há estado intermediário aceitável.
7. **Atualizar checkpoint final** com `status=sucesso`, `numero_documento`, `linhas_total=N`, `linhas_ok=N`, `salvo_em=<timestamp>`.

#### 6.7.3 Tratamento de erro por linha

| Cenário | Reação |
|---|---|
| Campo recusado pelo Protheus (ex.: produto inexistente) — popup de erro | Retry da **linha atual** até 3×: fechar popup, limpar célula, re-tentar. **Não avançar.** |
| `numero_serie` ausente na planilha | Validação pré-execução já barrou; nunca chega no runtime. Se chegar (defesa em profundidade): aborta com exit 3. |
| Auto-preenchimento de descrição não ocorre em 3s | Warn em log, prosseguir — a planilha pode trazer o valor manual; só falha se Protheus rejeitar `Tab`. |
| Após 3 retries da mesma linha, ainda falha | Marcar **a linha** como `status=falhou` no checkpoint, capturar evidência, **abortar a execução inteira** com exit 1. **Não pular linha** — pular linhas dentro do mesmo documento de transferência produz dado contábil incorreto. |
| Sessão expira no meio do preenchimento | Mesma regra de F5 (re-login), porém **o documento em INCLUIR é descartado pelo Protheus** ⇒ ao re-logar, o robô deve recomeçar a F7 do passo 1, com a planilha inteira. Checkpoint guarda `numero_documento` antigo apenas para auditoria de documento órfão. |

> **Diferença chave em relação a F4:** em F4, falha em técnico N **não impede** N+1 (downloads são independentes). Em F7, falha em linha N **aborta** as linhas N+1..N+M, porque todas pertencem ao mesmo documento atômico no Protheus. Esta é uma regra de produto, não técnica.

#### 6.7.4 Saída

- **Checkpoint:** `state/transferencia_multipla_AAAA-MM-DD.json` — modelo:
  ```json
  {
    "id_execucao": "2026-05-04T10:15:33_xxx",
    "planilha_origem": "referencias/trans_mult.xlsx",
    "planilha_sha256": "<hash>",
    "numero_documento": "YUXI000005MX1",
    "linhas_total": 12,
    "linhas_ok": 12,
    "status": "sucesso",
    "salvo_em": "2026-05-04T10:18:02"
  }
  ```
- **Log:** mesmo sink de F1–F6, com `etapa` em `["trans_mult.abrir", "trans_mult.linha", "trans_mult.salvar"]`.
- **Evidências de erro:** `logs/evidencias/trans_mult/<timestamp>_linha<N>.png`.

#### 6.7.5 Critérios de aceite

- [ ] Planilha válida com N linhas é processada e gera **um único** documento no Protheus com N linhas no grid.
- [ ] `Numero Documento` autogerado é capturado e gravado no checkpoint **antes** do início do preenchimento das linhas.
- [ ] Linha sem `numero_serie` na planilha provoca aborto **antes** do navegador abrir, com exit 3 e mensagem citando a linha ofensora.
- [ ] `Quantidade` é sempre o último campo escrito por linha; navegação entre linhas usa seta ↓ (não clique).
- [ ] Falha em qualquer linha após 3 retries aborta a execução inteira (exit 1) — robô **não** salva documento parcial.
- [ ] Toda linha gera 1 entrada de log com `etapa=trans_mult.linha`, `linha=N`, `numero_documento=<id>`.
- [ ] Toda falha de linha gera screenshot em `logs/evidencias/trans_mult/`.
- [ ] Re-execução com mesmo arquivo XLSX **não cria** documento duplicado se o anterior foi salvo com sucesso (idempotência via `planilha_sha256` no checkpoint do dia).
- [ ] Senha nunca aparece em nenhum log de F7.

#### 6.7.6 Impacto na arquitetura

**Reuso integral (sem mudanças):**
- `core/navegador.py` — viewport, contexto, screenshot, downloads (não usados, mas inofensivos).
- `core/visao.py` — template matching, OCR, multi-scale, threshold 0.70.
- `core/log.py` — sink loguru, bind de `etapa`/`tecnico` (esta última passa a aceitar valor `"-"` ou ser substituída por `documento` no contexto F7).
- `core/config.py` — adicionar `transferencia_xlsx_path: Path = Path("referencias/trans_mult.xlsx")` e `TRANSFERENCIA_XLSX` em `.env.example`.
- `core/estado.py` — checkpoint (escrita atômica já existente serve igual).
- F1 (`fazer_login`) e F2 (`navegar_ate_rotina`) em `core/acoes.py` — F2 ganha um parâmetro `rotina: Literal["mat_estoque", "trans_multipla"]` para escolher a referência (`08_*` vs `08.1_*`).

**Novos arquivos:**

| Arquivo | Papel |
|---|---|
| `core/planilha.py` | Leitura + validação do XLSX de entrada usando `openpyxl` (já é dep transitiva via XLSX, sem peso de `pandas`). Expõe `carregar_transferencias(path) -> list[LinhaTransferencia]` e levanta `PlanilhaInvalidaError` (mapeia para exit 3). |
| `core/schema.py` (estender) | Novo modelo `LinhaTransferencia` (Pydantic) com os 20 campos da §6.7.1; `Decimal` para `quantidade`/`potencia`. Novo modelo `CheckpointTransferenciaMultipla`. |
| `core/acoes.py` (estender) | Novas funções: `abrir_inclusao_trans_multipla(page)`, `capturar_numero_documento(page) -> str`, `preencher_linha_grid(page, linha: LinhaTransferencia) -> None`, `salvar_documento_trans_multipla(page) -> None`. Cada uma com `@retry(stop_after_attempt(3))`. |
| `flows/transferencia_multipla.py` | Orquestrador da feature: valida planilha → F1 → F2(rotina="trans_multipla") → loop de linhas → salvar → checkpoint. Análogo a `flows/processar_lista.py`. |
| `main.py` (estender) | Subcomando CLI: `python main.py trans-multipla [--planilha <path>]`. Padrão atual (`python main.py`) continua disparando o fluxo de download F1–F6. |

**Novas exceções** (em `core/acoes.py`, mesma família de `LoginError`/`NavegacaoError`):
- `PlanilhaInvalidaError` → exit 3, nunca tenta retry.
- `TransferenciaIncompletaError` → exit 1, levantada após 3 retries em uma linha; carrega `numero_documento` (órfão) e `linha_falha` para o log.

**Dependência adicional:** `openpyxl >= 3.1.5` em `requirements.txt` (somente leitura de XLSX; `pandas` permanece **não** listada — escolha consistente com §13.3/§13.4 de manter o stack mínimo).

**Não-impacto explícito:** F3–F6 (download por técnico), F5 (recuperação de sessão) e F6 (logging/evidências) seguem inalterados; F7 é um fluxo paralelo, não uma evolução do anterior.

---

## 7. Fluxos de Usuário

### 7.1 Fluxo principal — execução diária

```mermaid
flowchart TD
    Start([Operador: edita tecnicos.json]) --> Run["python main.py"]
    Run --> Boot[Carrega .env e config]
    Boot --> Open[Abre navegador Playwright]
    Open --> Login{Login OK?}
    Login -- não --> RetryLogin{tentativas < 3?}
    RetryLogin -- sim --> Login
    RetryLogin -- não --> Abort([Aborta exit 2])
    Login -- sim --> Loop[Itera tecnicos.json]
    Loop --> Check{já em checkpoint = sucesso?}
    Check -- sim --> NextTec[Próximo técnico]
    Check -- não --> Process[F2 navegar + F3 baixar]
    Process --> Valid{XLSX válido?}
    Valid -- sim --> Save[Atualiza checkpoint = sucesso]
    Valid -- não --> Retry{tentativas < 3?}
    Retry -- sim --> Recovery[Volta home + retry]
    Recovery --> Process
    Retry -- não --> Mark[Marca falhou + screenshot]
    Save --> NextTec
    Mark --> NextTec
    NextTec --> More{tem próximo?}
    More -- sim --> Loop
    More -- não --> Summary[Imprime resumo + log]
    Summary --> End([Exit 0 ou 1])
```

### 7.2 Fluxo de recuperação de sessão

```mermaid
flowchart LR
    Action[Tentando ação] --> Fail[Erro / timeout]
    Fail --> Detect{Tela de login?}
    Detect -- sim --> Relog[F1 Login]
    Relog --> Resume[Retoma técnico atual]
    Detect -- não --> RetryStep[Retry da etapa]
    RetryStep --> Action
```

### 7.3 Fluxo F7 — Transferência Múltipla baseada em Planilha

```mermaid
flowchart TD
    Start([Operador: prepara trans_mult.xlsx]) --> Run["python main.py trans-multipla"]
    Run --> Validate[core/planilha.py<br/>valida XLSX + cabeçalho + linhas obrigatórias]
    Validate --> ValidOk{válida?}
    ValidOk -- não --> Exit3([Aborta exit 3])
    ValidOk -- sim --> Login[F1 Login]
    Login --> Nav[F2 Favoritos -> Tranf. Multipla<br/>ref08.1]
    Nav --> Inc[Clicar +Incluir<br/>ref09.1]
    Inc --> Cap[Capturar Numero Documento<br/>ref11.1 + grava no checkpoint]
    Cap --> LoopStart[Foco na 1ª célula do grid<br/>ref10.1]
    LoopStart --> FillLine[Preenche linha N:<br/>prod_orig -> ... -> numero_serie -> ... -> quantidade]
    FillLine --> LineOk{linha aceita?}
    LineOk -- não --> RetryLine{retries < 3?}
    RetryLine -- sim --> FillLine
    RetryLine -- não --> AbortAll[Marca linha falhou + screenshot<br/>aborta documento]
    AbortAll --> Exit1([Exit 1<br/>documento NÃO salvo])
    LineOk -- sim --> Down[Seta ↓ próxima linha]
    Down --> More{tem próxima linha?}
    More -- sim --> FillLine
    More -- não --> Save[Clicar Salvar<br/>ref12.1]
    Save --> SaveOk{salvou?}
    SaveOk -- não --> Exit1
    SaveOk -- sim --> Cp[Checkpoint final<br/>status=sucesso + numero_documento]
    Cp --> End([Exit 0])
```

---

## 8. Sistema de Visão Computacional + MCP Context7

> **Observação:** este projeto não usa LLMs nem agentes de IA tradicionais. O análogo da seção "agentes de IA" do template Imersão é o **sistema de visão computacional** (matching + OCR) e a **integração opcional com MCP Context7** para apoio contextual.

### 8.1 Estratégia de Computer Vision

**Pipeline para "clicar em X":**
1. Captura screenshot atual via `page.screenshot(full_page=False)`.
2. Carrega referência (ex.: `referencias/13_clicar_planilha.png`) e extrai a região-âncora (recorte central da seta/elemento).
3. `cv2.matchTemplate` com `TM_CCOEFF_NORMED`; threshold inicial `0.85`.
4. Se `max_val >= threshold` → calcula centro do match → `page.mouse.click(x, y)`.
5. Se `max_val < threshold` → aumenta janela de busca (multi-scale: 0.9, 1.0, 1.1) → re-tenta.
6. Falhou todas as escalas → cai para OCR (procurar texto-âncora na referência).
7. OCR também falha → erro com screenshot.

**Cuidado especial com Canvas:**
- Coordenadas são em pixels da viewport. Sempre usar viewport fixa (ex.: 1366×768) para reprodutibilidade.
- Antes de matching, esperar "estado estático": comparar 2 screenshots com 500ms de intervalo; só prosseguir se idênticas (sistema parou de animar).

### 8.2 OCR (último recurso)

**Quando usar:**
- Validar que o nome do técnico que apareceu confere com o JSON.
- Ler mensagens de erro do Protheus quando aparecem em Canvas.

**Como:** recorte da região esperada → pré-processamento (greyscale + threshold) → `pytesseract.image_to_string(lang='por')` → comparação fuzzy (`rapidfuzz`).

### 8.3 MCP Context7 (opcional, fase 2)

**Hipótese de uso:**
Quando o robô não consegue identificar a tela atual (matching falha em todas as âncoras), enviar o screenshot atual para um servidor MCP Context7 que classifique:
- "Tela de login"
- "Home/Favoritos"
- "Filtro de técnico"
- "Diálogo de erro"
- "Desconhecido"

E retornar a próxima ação sugerida.

**Como integrar (sem implementar agora):**
- `core/contexto.py` expõe `classificar_tela(screenshot_path) -> {tela: str, acao_sugerida: str}`.
- Implementação inicial: stub que sempre retorna `desconhecido`.
- Integração real: chamar MCP Context7 via cliente HTTP/SDK.

**Critérios para ativar:**
- ❌ Não ativar no MVP (Sprint 1–6).
- ✅ Ativar somente se, após 2 semanas de operação, houver > 5% de execuções abortadas por "tela desconhecida".

---

## 9. Operação e Configuração

> Este projeto não é um SaaS — não há billing, planos ou usuários múltiplos. Esta seção cobre **operação** em vez disso.

### 9.1 Variáveis de ambiente (`.env`)

| Variável | Tipo | Obrigatória | Exemplo |
|---|---|---|---|
| `PROTHEUS_URL` | string (URL) | ✅ | `https://protheus.empresa.com.br/...` |
| `PROTHEUS_USER` | string | ✅ | `usuario.robo` |
| `PROTHEUS_PASS` | string (secret) | ✅ | (definido localmente, nunca em git) |
| `HEADLESS` | bool | ❌ (default: `false` em dev) | `true` |
| `VIEWPORT_W` | int | ❌ (default: `1366`) | `1366` |
| `VIEWPORT_H` | int | ❌ (default: `768`) | `768` |
| `DOWNLOAD_TIMEOUT_S` | int | ❌ (default: `60`) | `60` |
| `TECNICOS_JSON` | path | ❌ (default: `technicians.json` na raiz) | `technicians.json` ou `data/lote_a.json` |

### 9.2 Comandos CLI

```bash
# Execução completa
python main.py

# Apenas reprocessar falhas do checkpoint atual
python main.py --retry-falhos

# Modo headed (vê o navegador) — debug
HEADLESS=false python main.py

# Lote alternativo
TECNICOS_JSON=data/lote_b.json python main.py
```

### 9.3 Exit codes

| Código | Significado |
|---|---|
| 0 | Todos os técnicos baixados |
| 1 | Concluído com falhas individuais (parciais) |
| 2 | Aborto crítico (credenciais inválidas, sessão irrecuperável, **limite de conexões do Protheus excedido — ver F5.1**) |
| 3 | Erro de configuração (JSON inválido, env vars faltando) |

---

## 10. Design e UX (CLI + Logs)

Este projeto não tem UI gráfica. A "experiência" do operador é via terminal.

### 10.1 Saída no terminal (mockup)

```
╔═══════════════════════════════════════════════════╗
║  robo-totvs — Mat. Estoque por Técnico            ║
║  Execução: 2026-05-01 08:30:00                    ║
╚═══════════════════════════════════════════════════╝

[1/3] 000123 JOÃO DA SILVA
  → login OK
  → navegando até rotina... OK (4.2s)
  → preenchendo código... OK
  → baixando XLSX... OK (8.1s)
  → arquivo: downloads/2026-05-01/000123_JOAO_DA_SILVA.xlsx (24.3 KB)
  ✓ sucesso

[2/3] 000456 MARIA SOUZA
  → preenchendo código... OK
  → baixando XLSX... TIMEOUT (60s) — tentativa 1/3
  → recovery: voltando para home... OK
  → preenchendo código... OK
  → baixando XLSX... OK (12.4s)
  ✓ sucesso

[3/3] 000789
  → preenchendo código... ERRO: código inválido (popup Protheus)
  ✗ falhou após 3 tentativas — evidência: logs/evidencias/2026-05-01_083512_step3.png

═══════════════════════════════════════════════════
RESUMO  total: 3  sucesso: 2  falha: 1
duração: 02:14
log: logs/run-2026-05-01-083000.log
═══════════════════════════════════════════════════
```

### 10.2 Princípios de UX da CLI

- 1 linha por etapa, não verboso.
- Cores via loguru (verde sucesso, amarelo warning, vermelho erro).
- Progresso `[N/Total]` sempre visível.
- Caminho do arquivo de log impresso ao final.

### 10.3 Referências visuais (sistema-alvo)

Não há design system próprio — o robô interage com o Protheus existente. Os 18 prints em `referencias/` são **ground truth** de cada estado da UI. Qualquer mudança visual no Protheus que invalide essas referências exige re-captura.

**Inventário atual da pasta `referencias/`** (já presente no repositório, ground truth do passo a passo da automação):

| # | Arquivo | Etapa do fluxo |
|---|---|---|
| 01 | `01_link_de_acesso` | F1 — abrir URL inicial |
| 02 | `02_clicar_ok.png` | F1 — popup inicial |
| 03 | `03_insira_usuario.png` | F1 — campo usuário |
| 04 | `04_insira_senha.png` | F1 — campo senha |
| 05 | `05_clicar_entrar.png` | F1 — botão Entrar |
| 06 | `06_clicar_entrar.png` | F1 — confirmar empresa |
| 07 | `07_pagina_home_clicar_favoritos.png` | F2 — abrir Favoritos |
| 08 | `08_clicar_Mat_Estoque_Por_Tecnico.png` | F2 — selecionar rotina |
| 09 | `09_clicar_confirmar.png` | F2 — confirmar diálogo |
| 10 | `10_caso-aparececa_clicar_Nao_exibir_proximos_7_dias.png` | F2 — popup opcional 7 dias |
| 11 | `11_colocar_o_codigo_tecnico.png` | F3 — preencher `code` do técnico |
| 12 | `12_clicar_OK.png` | F3 — confirmar filtro |
| 13 | `13_clicar_planilha.png` | F3 — escolher Planilha |
| 14 | `14_clicar_tipo_de_planilha.png` | F3 — abrir dropdown |
| 15 | `15_clicar_Formato_de_Tabela_xlsx.png` | F3 — selecionar XLSX |
| 16 | `16_clicar_Imprimir.png` | F3 — disparar Imprimir |
| 17 | `17_clique_Sim.png` | F3 — confirmar geração |
| 18 | `18_aguarde_7seg_e_voltara_para_o_passo_07.png` | F3 — espera de 7s pós-download |
| 08.1 | `08.1_Tranf._Multipla.png` | F7 — selecionar rotina "Tranf. Multipla" no menu Favoritos |
| 09.1 | `09.1_Incluir.png` | F7 — clicar `+ Incluir` na tela `Transferencia Mod. II` |
| 10.1 | `10.1_loop_de_materail_baseado_na_planilha.png` | F7 — grid `Transferencia Mod. II - INCLUIR` (foco da 1ª célula) |
| 11.1 | `11.1_Salvar_codigo_do_documento.png` | F7 — capturar `Numero Documento` autogerado |
| 12.1 | `12.1_clicar_salvar.png` | F7 — clicar `Salvar` após preencher todas as linhas |

> **Nota:** o arquivo `01_link_de_acesso` no repositório está sem extensão (não é `.png`). Ao implementar `core/visao.py`, tratar este caso (renomear para `.png` ou aceitar match por nome sem extensão).
>
> **Convenção `*.1`:** referências da família `*.1_*.png` pertencem ao fluxo F7 (Transferência Múltipla) e **não substituem** as referências sem sufixo, que continuam servindo F1–F3 (download por técnico). Manter os dois conjuntos lado a lado.

---

## 11. APIs e Integrações

Este projeto **não expõe API HTTP** no MVP. Integrações:

| Integração | Direção | Tipo | Observação |
|---|---|---|---|
| TOTVS Protheus WebApp | saída (consome) | UI (Playwright) | Único sistema externo |
| Filesystem local | saída | Escrita de `.xlsx` e `.log` | Diretórios `downloads/`, `logs/`, `state/` |
| MCP Context7 (opcional) | saída (consome) | RPC/HTTP | Apenas se ativado em fase 2 |

**APIs futuras (fora do MVP):**
- Endpoint HTTP `POST /executar` para disparo via webhook.
- Endpoint `GET /status/<execucao_id>` para acompanhar progresso.
- Notificações Slack/Email ao final.

---

## 12. Roadmap de Sprints

> **Movido para [`TASKS.md`](./TASKS.md).** Toda lista de tarefas executáveis (sprints, checkboxes, demos) vive lá. Este PRD permanece como fonte de verdade para **decisões de produto, arquitetura, contratos de dados e UX**; o TASKS.md é a fonte de verdade para **o que fazer e em que ordem**.

---

## 13. Decisões Técnicas e Trade-offs

### 13.1 Playwright em vez de Selenium
**Decisão:** usar Playwright.
**Por quê:** suporte nativo a iFrames com API limpa (`frame_locator`), espera inteligente built-in (`wait_for_state`), captura de eventos de download mais confiável, melhor manuseio de páginas multi-contexto.
**Trade-off:** comunidade menor que Selenium para Protheus especificamente (poucos exemplos). Mitigação: a estratégia de visão computacional reduz a dependência da API do navegador, então Selenium vs Playwright importa menos.

### 13.2 Computer Vision como cidadão de primeira classe (não fallback raro)
**Decisão:** assumir que a maioria dos cliques será via template matching, não via DOM.
**Por quê:** Protheus WebApp roda via SmartClient HTML que renderiza tudo em Canvas. Tentar achar IDs estáveis no DOM é frágil e mudará a cada upgrade do Protheus. Os 18 prints já existem e são exatamente isso.
**Trade-off:** matching é sensível a resolução, tema e zoom. Mitigação: viewport fixa (1366×768) + multi-scale matching + re-captura documentada quando layout muda.

### 13.3 Sem Docker no MVP
**Decisão:** rodar direto em venv Python.
**Por quê:** restrição explícita do produto. Operador roda local, navegador precisa de display (ou Xvfb extra), simplicidade vence em fase 1.
**Trade-off:** "funciona na minha máquina" é risco. Mitigação: `requirements.txt` pinado + `.env.example` + README detalhado. Containerização entra em Sprint 9+ se for produção compartilhada.

### 13.4 Sem testes automatizados no MVP
**Decisão:** validação manual via demo ao final de cada sprint.
**Por quê:** restrição explícita; testar interação com sistema externo instável tem ROI baixo no MVP — qualquer mock seria mentiroso.
**Trade-off:** regressões silenciosas. Mitigação: cada sprint tem demo obrigatória + screenshots de evidência preservados.

### 13.5 Checkpoint em arquivo JSON, não em SQLite
**Decisão:** `state/checkpoint.json` simples.
**Por quê:** volume baixo (centenas de técnicos por dia), legibilidade humana, zero deps adicional. SQLite seria over-engineering.
**Trade-off:** corrupção se processo morrer durante a escrita. Mitigação: escrita atômica (write-to-temp + rename).

### 13.6 OCR como último recurso, não como camada principal
**Decisão:** OCR só quando matching falha.
**Por quê:** OCR é lento (segundos), errado em ~5–15% dos casos com fontes pequenas, exige Tesseract instalado no sistema (não pip-only). Template matching é determinístico e rápido.
**Trade-off:** template matching falha quando o Protheus muda layout. Mitigação aceita: documentar processo de re-captura de referências.

### 13.7 Sem LLM no MVP
**Decisão:** não usar GPT/Claude para decidir cliques.
**Por quê:** custo recorrente, latência (segundos por chamada × N técnicos), determinismo importa mais que flexibilidade aqui — o fluxo é fixo, só a UI é instável.
**Trade-off:** quando matching falha, robô falha. Mitigação: MCP Context7 como upgrade controlado em fase 2 — só ativa se métrica real justificar.

### 13.8 Sleep fixo de 7s no passo 18 (exceção à regra)
**Decisão:** após "Sim" de imprimir, dormir ~7s antes de validar volta para home.
**Por quê:** o operador documentou ("aguarde 7seg e voltará para o passo 07") que esse intervalo é estável e a tela durante esses 7s não tem evento detectável. Tentar polling agressivo aumentaria o risco de clicar prematuramente.
**Trade-off:** desperdiça segundos. Aceito — robô não tem SLA de latência.

### 13.9 Decisão NO-GO sobre MCP Context7 (Sprint 7)
**Decisão:** Não implementar a integração real com MCP Context7 para classificação de tela desconhecida. O sistema de visão continua retornando exceção nativa no caso de timeout/falha.
**Por quê:** Ao final da Sprint 6 (MVP atingido), a análise de logs em produção demonstrou 0% de falhas provocadas por estado de tela irreconhecível (`"tela desconhecida"` ou `"matching falhou"` persistente sem recuperação). O protocolo manda implementar a ferramenta de IA apenas se ultrapassasse 5% de abortos críticos (PRD §8.3).
**Trade-off:** Menor capacidade de diagnosticar mudanças completas na UI sem ver o screenshot. Aceito — a evidência via screenshot armazenada no disco já fornece contexto suficiente para o desenvolvedor manter os scripts.

### 13.10 F7 aborta documento inteiro em vez de salvar parcial
**Decisão:** Em F7, falha em qualquer linha após 3 retries provoca aborto da execução **sem salvar** o documento de transferência. Não há equivalente do "pula técnico falho e segue" de F4.
**Por quê:** F4 é read-only (export); um técnico falho não corrompe nada — basta reprocessar. F7 é write — um documento de transferência salvo com 11 de 12 linhas é dado contábil errado em produção, e desfazer exige operador humano abrindo o Protheus para cancelar/estornar. Salvar parcial transfere o custo de erro do robô para o operador. Inverter: aborto cedo, sem registro persistente no Protheus.
**Trade-off:** Uma única linha mal-formada bloqueia toda a planilha. Mitigação: validação pré-execução (§6.7.1) detecta a maior parte dos erros antes do navegador abrir; o que resta são erros do próprio Protheus (saldo, produto inexistente), que de qualquer forma exigem ação humana.

### 13.11 `openpyxl` em vez de `pandas` para leitura da planilha
**Decisão:** Usar `openpyxl` direto, sem `pandas`.
**Por quê:** A leitura é estritamente sequencial (linha-a-linha), o volume é pequeno (dezenas de linhas), e o checkpoint usa `Decimal` para `quantidade`/`potencia` — exatamente o caso em que `pandas` (que joga tudo para `float64`) atrapalha. `pandas` adiciona ~50MB de deps e BLAS, sem benefício para o caso.
**Trade-off:** Se um dia houver agregações ou múltiplas abas, `pandas` ficaria conveniente. Aceito — refatorar quando o caso aparecer, não preventivamente.

### 13.13 Limite de conexões do Protheus: aborto duro em vez de tentar contornar
**Decisão:** Quando o modal "Limite de conexoes do Usuario excedido" aparece e persiste após `Fechar`, o robô levanta `SessaoEsgotadaError` e termina com exit 2. **Não** insistimos em re-logar, não esperamos timeout no servidor, não trocamos de usuário.
**Por quê:** O modal é sinal de um problema de licença/ambiente (sessões fantasma do próprio robô, ou outro processo no mesmo usuário), não de transiente recuperável. Re-login imediato cai no mesmo erro porque o servidor ainda enxerga as sessões antigas. Esperar timeout (`>30 min`) trava o operador sem visibilidade do que está acontecendo. Aborto cedo + mensagem que aponta a ação correta (pedir ao admin para matar as sessões) tem ROI muito maior do que qualquer tentativa de auto-cura.
**Trade-off:** Se um dia o servidor recuperar sozinho em poucos segundos, o robô vai abortar prematuramente. Mitigação: a re-execução é idempotente (checkpoint preserva pendentes) — basta o operador rodar de novo após resolver a causa. Em produção (Sprint 8.1) esse fluxo manual leva < 2 min e dá visibilidade do problema raiz, em vez de mascarar com retries.

### 13.14 Técnicos não-tentados ficam `pendente`, não `falhou`
**Decisão:** Quando o loop aborta por `SessaoEsgotadaError`, os técnicos que ainda não foram tentados **não** são contados como falha — permanecem com status `pendente` no checkpoint.
**Por quê:** "Falha" tem semântica específica no checkpoint (= tentativa real que não baixou XLSX, elegível para `--retry-falhos`). Contar não-tentados como falha mascara a real taxa de erro do robô e força o operador a usar `--retry-falhos` para retomar — o que reprocessa também as falhas verdadeiras. Manter como `pendente` permite retomada idempotente simples (`python main.py` sem flags) e relatórios honestos. O bug original (Sprint 8.1) reportava "14 falhas" quando na verdade eram 0 falhas reais + 14 não-tentados.
**Trade-off:** Resumo final agora pode mostrar `total != sucesso + falha + pulados` quando há aborto. Aceito — o operador prefere a verdade ("aborto após 19 OK, 14 pendentes") a uma simetria contábil falsa ("19 OK, 14 falha").

### 13.12 `Quantidade` como último campo escrito por linha
**Decisão:** A ordem de preenchimento dos campos do grid é fixa, com `quantidade` sempre por último, encerrada com `Enter`/`Tab` final.
**Por quê:** Comportamento observado no Protheus: ao digitar valor em `Quantidade` e confirmar, o foco salta para a primeira coluna da próxima linha. Tentar usar essa transição como confirmação de linha + avanço de cursor é mais robusto do que clicar manualmente em cada célula da próxima linha.
**Trade-off:** Acopla a implementação a uma regra de UI do Protheus que pode mudar com upgrade. Mitigação: `core/acoes.py::preencher_linha_grid` isola a regra; mudança de comportamento é localizada.

---

## 14. Glossário

| Termo | Definição |
|---|---|
| **Protheus** | ERP da TOTVS, suíte modular para gestão empresarial |
| **SmartClient HTML** | Cliente web do Protheus que renderiza a interface via Canvas + JS |
| **Canvas** | Elemento HTML5 `<canvas>` — renderização gráfica baseada em pixels, sem DOM acessível para os elementos pintados |
| **iFrame** | Frame HTML aninhado; no Protheus, várias rotinas abrem em iFrames separados |
| **Rotina** | Funcionalidade do Protheus (ex.: "Mat Estoque Por Técnico") |
| **Técnico** | Funcionário do almoxarifado/campo cujo estoque é consultado |
| **Template Matching** | Técnica de OpenCV (`cv2.matchTemplate`) que localiza a posição de uma imagem-modelo dentro de uma imagem maior |
| **OCR** | Optical Character Recognition — reconhecimento de texto em imagens |
| **MCP Context7** | Protocolo de Modelo de Contexto v7 — usado opcionalmente para classificação de tela |
| **Checkpoint** | Estado persistido em disco do progresso da execução (técnicos processados / pendentes / falhos) |
| **Headless** | Modo do navegador sem janela gráfica (mais rápido, sem display necessário) |
| **Viewport** | Dimensão lógica da janela do navegador — fixar em 1366×768 garante reprodutibilidade dos prints |
| **Idempotência** | Propriedade de poder executar a mesma operação N vezes com o mesmo efeito de 1 vez (re-execuções não duplicam downloads) |
| **Tranf. Multipla** | Rotina do módulo Estoque/Custos do Protheus que cria um único documento contendo várias linhas de transferência de material entre armazéns/produtos. Alvo da feature F7. |
| **Numero Documento** | Identificador autogerado pelo Protheus ao abrir uma nova Transferência Múltipla (ex.: `YUXI000005MX1`). Persistido no checkpoint para auditoria mesmo em caso de aborto. |
| **Documento órfão** | Documento de transferência aberto no Protheus mas não salvo (porque o robô abortou no meio do preenchimento). Não persiste no banco do ERP, mas o número fica registrado em log para o operador conferir manualmente. |
| **Linha do grid** | Cada linha do grid `Transferencia Mod. II - INCLUIR`, equivalente a uma linha da planilha de entrada. |
| **Limite de conexões do Usuário** | Teto de sessões simultâneas por usuário imposto pela licença do Protheus. Quando atingido, o ERP injeta um modal HELP bloqueante; o robô detecta e aborta com exit 2 (ver F5.1, §13.13). |
| **Sessão fantasma** | Sessão server-side do Protheus que ficou aberta mesmo após o robô fechar o navegador (não houve logout explícito). Acumula-se a cada execução abortada, eventualmente atingindo o "Limite de conexões do Usuário". Mitigação atual: ação do admin via Monitor de Conexões. |
| **Técnico pendente vs falho** | `pendente` = não tentado ainda (ou aborto sistêmico antes da vez dele); `falho` = tentado, mas o XLSX não foi entregue após 3 retries. Apenas `falho` é elegível para `--retry-falhos` (ver §13.14). |

---

**Fim do PRD.**

> Próximo passo: validar este documento, então iniciar **Sprint 1** (setup + bootstrap). Não escrever código antes da aprovação do PRD.
