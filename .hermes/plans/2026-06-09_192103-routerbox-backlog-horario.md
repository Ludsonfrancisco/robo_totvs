# RouterBox Backlog Hourly Download Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Criar no `robo_totvs` um fluxo RouterBox que baixe Backlog ACERTA + LOGA de hora em hora, valide os XLSX, consolide em um único arquivo compatível com o `dmais_portal` e deixe rastreabilidade/logs para operação.

**Architecture:** O `robo_totvs` continua responsável por RPA/browser automation e consolidação do XLSX. O `dmais_portal` continua responsável pela importação e regras de Backlog/Prazo. A execução horária deve rodar no `worker.py`, compartilhando o mesmo container EasyPanel, mas com artefatos e logs separados do fluxo TOTVS diário para não quebrar a rotina atual de estoque.

**Tech Stack:** Python 3.11, Playwright/Chrome, pandas/openpyxl, Docker Swarm/EasyPanel, volume compartilhado `/app/data_pipeline`, Django management command existente no portal (`importar_backlog`).

---

## 1. Contexto atual

### Já validado em teste real

- RouterBox ACERTA e LOGA foram acessados com Playwright/Chrome dentro do container `apps_robo_totvs`.
- Downloads funcionaram com `channel="chrome"`.
- Modal de novidades precisa ser fechado antes de clicar no menu.
- O link `Baixar` deve ser aguardado via polling, não sleep fixo.
- XLSX baixados no teste:
  - ACERTA: ~556 linhas.
  - LOGA: ~3924 linhas.
- Arquivo consolidado importado manualmente no `apps_dmais`:
  - Total importado: 4002 ordens.
  - Última OS: 4265301, `09/06/2026 15:43:59`.

### Decisões já tomadas

- O robô fica no `robo_totvs`, não no `dmais_portal`.
- A primeira versão não filtra nem deduplica: apenas unifica ACERTA + LOGA.
- O campo `Fluxo` precisa ser normalizado para código numérico (`1.43`, `1.25`, etc.) por compatibilidade com o model atual do portal (`max_length=10`).
- Preservar `Fluxo Original` para auditoria.
- Sheet de dados precisa manter o nome `Relatório de Atendimentos`, porque o importador do portal lê esse nome.

### Regras de segurança operacional

- Nunca logar senha RouterBox.
- Não misturar artefatos RouterBox com `run.done`, `signal.ready`, `entrada/<data>` do fluxo TOTVS de estoque sem nomear claramente.
- Não quebrar o scheduler diário atual do TOTVS.
- Antes de qualquer patch/write_file em código: mostrar diff e pedir aprovação.
- Depois de cada mudança: rodar validação manual ou automatizada.

---

## 2. Escopo da sprint

### Dentro do escopo

1. Adicionar configuração RouterBox ao `robo_totvs`.
2. Criar fluxo `flows/routerbox_backlog.py` com:
   - login por instância;
   - fechamento de modal;
   - navegação até Atendimentos > Execução;
   - aplicação do filtro salvo;
   - export Excel;
   - polling do link `Baixar`;
   - validação XLSX.
3. Criar consolidador ACERTA + LOGA com normalização de `Fluxo`.
4. Criar CLI `python main.py routerbox-backlog`.
5. Adicionar execução horária no `worker.py` sem interferir no TOTVS diário.
6. Criar logs e manifesto JSON para cada execução horária.
7. Validar no container real com uma execução manual.
8. Validar importação no `apps_dmais` usando o arquivo consolidado.
9. Preparar instruções de deploy EasyPanel.

### Fora do escopo desta sprint

- Alterar schema do `dmais_portal` para aceitar `Fluxo` longo.
- Deduplicação entre ACERTA/LOGA.
- Dashboard novo de status do RouterBox no portal.
- Botão manual no portal para disparar RouterBox.
- Alertas Telegram/WhatsApp se falhar.

---

## 3. Estrutura de arquivos proposta

### Criar

- `flows/routerbox_backlog.py`
- `tests/test_routerbox_backlog_consolidacao.py`
- `tests/fixtures/routerbox_acerta_sample.xlsx`
- `tests/fixtures/routerbox_loga_sample.xlsx`
- `docs/routerbox-backlog-hourly.md`

### Modificar

- `core/config.py`
- `main.py`
- `worker.py`
- `.env.example`
- `requirements.txt` se `pandas` ainda não estiver disponível no projeto local/container.

### Artefatos runtime esperados

Dentro do container:

```text
/app/data_pipeline/routerbox_backlog/
├── latest/
│   ├── acerta_backlog.xlsx
│   ├── loga_backlog.xlsx
│   ├── BACKLOG-GERAL-CONSOLIDADO-latest.xlsx
│   └── manifest.json
├── runs/
│   └── YYYY-MM-DD/HHMMSS/
│       ├── acerta_backlog.xlsx
│       ├── loga_backlog.xlsx
│       ├── BACKLOG-GERAL-CONSOLIDADO-YYYY-MM-DD-HHMMSS.xlsx
│       ├── manifest.json
│       └── routerbox.log
└── routerbox_backlog.ready
```

Observação: `routerbox_backlog.ready` é um trigger separado para não conflitar com `signal.ready` do fluxo de estoque.

---

## 4. Configuração proposta

Adicionar ao `core/config.py`:

```python
ROUTERBOX_USER: str | None = None
ROUTERBOX_PASS: str | None = None
ROUTERBOX_ACERTA_URL: str = "https://integra.acertasolucoes.net.br/routerbox/app_login/index.php"
ROUTERBOX_LOGA_URL: str = "https://integra.loga.net.br/routerbox/app_login/index.php"
ROUTERBOX_FILTER_ACERTA: str = "..#### BACKLOG GERAL ACERTA ####"
ROUTERBOX_FILTER_LOGA: str = "..#### BACKLOG GERAL LOGA ####"
ROUTERBOX_OUTPUT_DIR: str = "/app/data_pipeline/routerbox_backlog"
ROUTERBOX_DOWNLOAD_TIMEOUT_S: int = 180
ROUTERBOX_HOURLY_ENABLED: bool = True
ROUTERBOX_INTERVAL_MINUTES: int = 60
ROUTERBOX_RUN_ON_START: bool = False
```

Adicionar no `.env.example` sem senha real:

```env
# RouterBox Backlog hourly automation
ROUTERBOX_USER=
ROUTERBOX_PASS=
ROUTERBOX_ACERTA_URL=https://integra.acertasolucoes.net.br/routerbox/app_login/index.php
ROUTERBOX_LOGA_URL=https://integra.loga.net.br/routerbox/app_login/index.php
ROUTERBOX_FILTER_ACERTA=..#### BACKLOG GERAL ACERTA ####
ROUTERBOX_FILTER_LOGA=..#### BACKLOG GERAL LOGA ####
ROUTERBOX_OUTPUT_DIR=/app/data_pipeline/routerbox_backlog
ROUTERBOX_DOWNLOAD_TIMEOUT_S=180
ROUTERBOX_HOURLY_ENABLED=true
ROUTERBOX_INTERVAL_MINUTES=60
ROUTERBOX_RUN_ON_START=false
```

---

## 5. Plano de tarefas

### Task 1: Preparar fixtures mínimos para testes de consolidação

**Objective:** Criar fixtures pequenas que simulem os dois XLSX RouterBox sem depender de login real.

**Files:**
- Create: `tests/fixtures/routerbox_acerta_sample.xlsx`
- Create: `tests/fixtures/routerbox_loga_sample.xlsx`
- Create: `tests/create_routerbox_fixtures.py` ou helper dentro do teste.

**Steps:**
1. Criar dois XLSX com sheets:
   - `Resumo`
   - `Relatório de Atendimentos`
2. Incluir colunas reais mínimas:
   - `Numero`
   - `Cliente`
   - `Fluxo`
   - `Data AB`
   - `Hora AB`
   - `Tel. Cel.`
3. Em uma linha, usar `Fluxo = #1.43 VAR DESCONECTA GERAL -ME`.
4. Validar que `openpyxl.load_workbook()` abre os dois arquivos.

**Verification:**

```bash
python3 -m pytest tests/test_routerbox_backlog_consolidacao.py -q
```

Expected inicialmente: falhar porque o consolidador ainda não existe.

---

### Task 2: Criar teste RED para normalização de Fluxo

**Objective:** Garantir que o consolidado preserva `Fluxo Original` e normaliza `Fluxo` para caber no portal.

**Files:**
- Create/Modify: `tests/test_routerbox_backlog_consolidacao.py`
- Future source: `flows/routerbox_backlog.py`

**Test case:**

```python
def test_consolidar_normaliza_fluxo_e_preserva_original(tmp_path):
    from flows.routerbox_backlog import consolidar_backlogs

    out = tmp_path / "BACKLOG-GERAL-CONSOLIDADO.xlsx"
    consolidar_backlogs(
        acerta_path="tests/fixtures/routerbox_acerta_sample.xlsx",
        loga_path="tests/fixtures/routerbox_loga_sample.xlsx",
        output_path=out,
    )

    df = pandas.read_excel(out, sheet_name="Relatório de Atendimentos", dtype=str)
    assert "Fluxo Original" in df.columns
    assert df["Fluxo"].str.len().max() <= 10
    assert "1.43" in set(df["Fluxo"].dropna())
```

**Run:**

```bash
python3 -m pytest tests/test_routerbox_backlog_consolidacao.py::test_consolidar_normaliza_fluxo_e_preserva_original -q
```

Expected: FAIL até implementar `consolidar_backlogs`.

---

### Task 3: Implementar consolidador puro

**Objective:** Implementar apenas a parte sem browser: leitura, validação, concatenação, normalização e escrita do XLSX.

**Files:**
- Create: `flows/routerbox_backlog.py`

**Core functions:**

```python
def validar_xlsx(path: Path) -> None:
    ...

def normalizar_fluxo_coluna(df: pandas.DataFrame) -> pandas.DataFrame:
    ...

def consolidar_backlogs(acerta_path: Path, loga_path: Path, output_path: Path) -> dict:
    ...
```

**Rules:**
- Validar `zipfile.is_zipfile(path)`.
- Ler sheet `Relatório de Atendimentos`.
- Concatenar ACERTA + LOGA.
- Adicionar `Origem RouterBox` com `ACERTA`/`LOGA`.
- Adicionar `Fluxo Original` antes de normalizar.
- Escrever sheet `Resumo` + `Relatório de Atendimentos`.
- Retornar resumo com contagens e última `Data AB + Hora AB`.

**Verification:**

```bash
python3 -m pytest tests/test_routerbox_backlog_consolidacao.py -q
python3 -m py_compile flows/routerbox_backlog.py
```

Expected: PASS.

---

### Task 4: Adicionar settings RouterBox

**Objective:** Centralizar URLs, filtros, credenciais e output dir no `core/config.py`.

**Files:**
- Modify: `core/config.py`
- Modify: `.env.example`

**Validation:**

```bash
python3 - <<'PY'
from core.config import settings
print(settings.ROUTERBOX_ACERTA_URL)
print(settings.ROUTERBOX_OUTPUT_DIR)
PY
```

Expected:
- imprimir URL ACERTA;
- imprimir `/app/data_pipeline/routerbox_backlog` ou valor configurado.

---

### Task 5: Implementar browser flow de uma instância

**Objective:** Baixar um XLSX de uma instância RouterBox usando Playwright.

**Files:**
- Modify: `flows/routerbox_backlog.py`

**Suggested API:**

```python
@dataclass(frozen=True)
class RouterBoxInstance:
    name: Literal["ACERTA", "LOGA"]
    url: str
    filter_label: str


def baixar_backlog_routerbox(
    page,
    instance: RouterBoxInstance,
    destino: Path,
    usuario: str,
    senha: str,
    timeout_s: int = 180,
) -> Path:
    ...
```

**Implementation notes:**
- Usar `channel="chrome"` via navegador existente ou contexto próprio.
- Não logar senha.
- Depois do login, fechar modal `.modal_menu .closed span` se existir.
- Navegar hamburger > Atendimentos > Execução.
- Entrar no iframe `app_menu_iframe` quando necessário.
- Selecionar filtro salvo por label.
- Clicar pesquisar.
- Clicar grupo Excel.
- Poll até `Baixar`/`.xlsx` por até 180s.
- Salvar no path `destino`.
- Validar XLSX após download.

**Manual validation ACERTA only:**

```bash
python3 main.py routerbox-backlog --only acerta --no-consolidate
```

Expected:
- arquivo `acerta_backlog.xlsx` criado;
- log não contém senha;
- XLSX válido.

---

### Task 6: Criar CLI `routerbox-backlog`

**Objective:** Permitir execução manual do novo fluxo sem passar pelo scheduler.

**Files:**
- Modify: `main.py`
- Modify: `flows/routerbox_backlog.py`

**Command target:**

```bash
python3 main.py routerbox-backlog
python3 main.py routerbox-backlog --only acerta
python3 main.py routerbox-backlog --only loga
python3 main.py routerbox-backlog --output-dir /tmp/routerbox-test
```

**Exit codes:**
- `0`: ACERTA + LOGA + consolidação OK.
- `1`: falha parcial ou import/consolidação incompleta.
- `2`: credenciais inválidas/sessão irrecuperável RouterBox.
- `3`: configuração inválida.

**Validation:**

```bash
python3 main.py --help
python3 main.py routerbox-backlog --help
```

Expected:
- subcomando aparece;
- help lista `--only`, `--output-dir`, `--no-consolidate`.

---

### Task 7: Manifesto e logs da execução

**Objective:** Produzir rastreabilidade para cada execução horária.

**Files:**
- Modify: `flows/routerbox_backlog.py`

**Manifest format:**

```json
{
  "success": true,
  "started_at": "2026-06-09T19:00:00Z",
  "finished_at": "2026-06-09T19:02:31Z",
  "instances": {
    "ACERTA": {"success": true, "path": "...", "rows": 556},
    "LOGA": {"success": true, "path": "...", "rows": 3924}
  },
  "consolidated": {
    "path": ".../BACKLOG-GERAL-CONSOLIDADO-2026-06-09-190000.xlsx",
    "rows": 4478,
    "latest_data_ab": "2026-06-09T15:43:59-03:00"
  },
  "errors": []
}
```

**Validation:**

```bash
python3 -m json.tool /app/data_pipeline/routerbox_backlog/latest/manifest.json
```

Expected: JSON válido.

---

### Task 8: Handoff para portal via volume compartilhado

**Objective:** Deixar o arquivo consolidado em local estável para o portal importar.

**Files:**
- Modify: `flows/routerbox_backlog.py`
- Possibly modify later in portal, but not in this sprint unless aprovado.

**Output stable paths:**

```text
/app/data_pipeline/routerbox_backlog/latest/BACKLOG-GERAL-CONSOLIDADO-latest.xlsx
/app/data_pipeline/routerbox_backlog/latest/manifest.json
/app/data_pipeline/routerbox_backlog/routerbox_backlog.ready
```

**Validation manual no portal:**

```bash
DMAIS=$(docker ps --filter name=apps_dmais --format '{{.Names}}' | head -1)
docker exec "$DMAIS" python manage.py importar_backlog /app/data_pipeline/routerbox_backlog/latest/BACKLOG-GERAL-CONSOLIDADO-latest.xlsx
```

Expected:
- importação conclui;
- `get_ultima_abertura_hoje()` retorna a última hora do arquivo.

---

### Task 9: Scheduler horário no `worker.py`

**Objective:** Rodar RouterBox a cada hora sem bloquear o scheduler diário TOTVS e sem depender de Hermes cron.

**Files:**
- Modify: `worker.py`

**Design:**
- Manter scheduler diário TOTVS intacto.
- Adicionar novo próximo evento `next_routerbox_run`.
- Loop principal deve dormir até o menor entre:
  - próximo TOTVS diário;
  - próximo RouterBox horário;
  - `run.signal` do portal.
- `run.signal` continua prioridade para retry TOTVS.
- RouterBox deve ter lock próprio para evitar sobreposição se uma execução demorar mais de 1h.

**Proposed functions:**

```python
def _routerbox_enabled() -> bool: ...
def _next_routerbox_run_at(now: datetime | None = None) -> datetime: ...
def _run_routerbox_once(mode: str = "hourly") -> None: ...
def _sleep_until_next_event(totvs_at: datetime, routerbox_at: datetime | None) -> str: ...
```

**Lock file:**

```text
/app/data_pipeline/routerbox_backlog/.routerbox.lock
```

**Validation fast mode:**

Temporariamente em container/dev:

```bash
ROUTERBOX_INTERVAL_MINUTES=5 ROUTERBOX_RUN_ON_START=true python3 worker.py
```

Expected:
- RouterBox roda no start;
- depois agenda próxima execução;
- TOTVS daily continua logado como próximo evento separado.

---

### Task 10: Teste real no container `apps_robo_totvs`

**Objective:** Validar o fluxo completo com Chrome real no ambiente onde vai rodar.

**Files:**
- None, only runtime validation.

**Commands:**

```bash
ROBO=$(docker ps --filter name=apps_robo_totvs --format '{{.Names}}' | head -1)
docker cp flows/routerbox_backlog.py "$ROBO:/app/flows/routerbox_backlog.py"
docker cp main.py "$ROBO:/app/main.py"
docker cp core/config.py "$ROBO:/app/core/config.py"
docker exec "$ROBO" python main.py routerbox-backlog --output-dir /app/data_pipeline/routerbox_backlog/manual-test
```

**Expected:**
- ACERTA download OK.
- LOGA download OK.
- Consolidado criado.
- Manifest OK.
- Nenhuma senha em log.

---

### Task 11: Teste de importação no `apps_dmais`

**Objective:** Provar que o arquivo gerado pelo robô entra no portal sem ajuste manual.

**Commands:**

```bash
DMAIS=$(docker ps --filter name=apps_dmais --format '{{.Names}}' | head -1)
docker exec "$DMAIS" python manage.py importar_backlog /app/data_pipeline/routerbox_backlog/latest/BACKLOG-GERAL-CONSOLIDADO-latest.xlsx

docker exec "$DMAIS" python manage.py shell -c "from prazo_atendimento.services import get_ultima_abertura_hoje; from django.utils import timezone; o=get_ultima_abertura_hoje(); print(o.numero_ordem if o else None); print(timezone.localtime(o.data_ab).strftime('%d/%m/%Y %H:%M:%S') if o else None)"
```

**Expected:**
- import sem erro;
- última atualização aparece coerente nas páginas Backlog/Prazo.

---

### Task 12: Deploy EasyPanel

**Objective:** Aplicar em produção sem quebrar o worker atual.

**Files:**
- `Dockerfile` só se `pandas` precisar entrar no `requirements.txt`.
- `requirements.txt` se necessário.
- EasyPanel env vars.

**Steps:**
1. Atualizar `.env`/EasyPanel com RouterBox vars.
2. Stop do serviço `apps_robo_totvs`.
3. Aguardar container sair.
4. Start/Continue no EasyPanel.
5. Verificar container ativo.
6. Verificar logs do worker.
7. Rodar um manual `python main.py routerbox-backlog` se não usar `ROUTERBOX_RUN_ON_START=true`.

**Validation:**

```bash
docker service logs apps_robo_totvs --tail 100
ROBO=$(docker ps --filter name=apps_robo_totvs --format '{{.Names}}' | head -1)
docker exec "$ROBO" date
docker exec "$ROBO" python main.py routerbox-backlog --help
```

Expected:
- timezone `America/Sao_Paulo`;
- worker sem crash loop;
- subcomando disponível.

---

## 6. Critérios de aceite

A sprint só está pronta quando:

1. `python3 -m pytest tests/test_routerbox_backlog_consolidacao.py -q` passa.
2. `python3 main.py routerbox-backlog --help` mostra o subcomando.
3. Execução manual no `apps_robo_totvs` baixa ACERTA e LOGA.
4. XLSX consolidado é válido (`zipfile.is_zipfile`).
5. XLSX consolidado tem sheet `Relatório de Atendimentos`.
6. `Fluxo` no consolidado tem código curto (`max len <= 10`).
7. `Fluxo Original` preserva o texto original quando existir.
8. Manifest JSON é criado.
9. Importação no `apps_dmais` conclui sem `value too long`.
10. Backlog/Prazo mostram a última atualização baseada no import.
11. Worker agenda a próxima execução horária sem afetar o TOTVS diário.
12. Logs não contêm senha RouterBox.

---

## 7. Riscos e mitigação

### Risco 1: RouterBox muda DOM/selectors

Mitigação:
- manter selectors em constantes no topo de `flows/routerbox_backlog.py`;
- salvar screenshot de falha em `logs/evidencias/routerbox/`;
- usar fallback por texto quando possível.

### Risco 2: LOGA demora mais de 180s para gerar XLSX

Mitigação:
- timeout configurável `ROUTERBOX_DOWNLOAD_TIMEOUT_S`;
- manifest registra timeout por instância;
- execução seguinte tenta novamente.

### Risco 3: Execução horária sobrepor execução anterior

Mitigação:
- lock `.routerbox.lock`;
- se lock ativo e processo recente, pular ciclo e logar `skipped_locked`.

### Risco 4: Conflito com fluxo TOTVS diário

Mitigação:
- não reutilizar `run.done`/`signal.ready` do estoque;
- artefatos RouterBox em `/app/data_pipeline/routerbox_backlog`;
- em `worker.py`, eventos independentes.

### Risco 5: `pandas` não instalado no container final

Mitigação:
- verificar no container antes;
- se ausente, adicionar `pandas>=2.2` ao `requirements.txt` e rebuild.

---

## 8. Ordem sugerida de execução

1. Task 1-3: consolidador com testes locais.
2. Task 4: settings/env.
3. Task 5-6: browser flow + CLI manual.
4. Task 7-8: manifest + handoff.
5. Task 10-11: teste real no container + import portal.
6. Task 9: scheduler horário no worker.
7. Task 12: deploy EasyPanel.

Motivo: primeiro garantir que o arquivo gerado é compatível com o portal; depois automatizar o horário. Isso reduz risco de um scheduler ficar repetindo arquivo inválido de hora em hora.

---

## 9. Próximo passo imediato

Começar pela Task 1 + Task 2 + Task 3 em TDD:

1. Criar fixtures pequenas.
2. Criar teste RED do consolidador.
3. Implementar `consolidar_backlogs()`.
4. Rodar pytest.
5. Mostrar diff antes de aplicar no código real, conforme padrão do projeto.

Quando aprovado, seguimos para o fluxo Playwright RouterBox real.
