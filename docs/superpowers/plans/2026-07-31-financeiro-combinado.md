# Financeiro Combinado Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar uma imagem de `dmais_automacoes` que execute Financeiro Hoje e Medição LOGA sem remover RouterBox ou Multiplica.

**Architecture:** Recuperar o fluxo comprovado da imagem anterior para o Git e registrá-lo no worker atual, que já contém a Medição. Manter roots e contratos de publicação independentes, com despacho síncrono e proteção de navegador.

**Tech Stack:** Python 3.10+, Playwright, openpyxl, pytest/unittest, Docker Swarm/EasyPanel.

---

### Task 1: Versionar a capacidade Financeiro Hoje recuperada

**Files:**
- Create: `flows/financeiro_hoje/*.py`
- Create: `flows/routerbox_coordination.py`
- Create: `scripts/financeiro_hoje_dom_probe.py`
- Create: `tests/test_financeiro_hoje_*.py`
- Create: `tests/test_routerbox_coordination.py`
- Create: `tests/fixtures/financeiro_hoje_personalizados.html`
- Create: `docs/financeiro-hoje-runbook.md`

- [ ] **Step 1: Comparar os SHA-256 com a imagem funcional**

Run: `sha256sum flows/financeiro_hoje/*.py flows/routerbox_coordination.py scripts/financeiro_hoje_dom_probe.py`

Expected: hashes iguais aos registrados durante a extração da imagem `financeiro-retry-4c31dd8`.

- [ ] **Step 2: Executar os testes originais recuperados**

Run: `.venv/Scripts/python -m pytest -q tests/test_financeiro_hoje_*.py tests/test_routerbox_coordination.py`

Expected: `141 passed, 10 skipped` ou contagem superior após novos testes, sem falhas.

- [ ] **Step 3: Versionar somente arquivos sem segredos**

Run: `git diff --check && git grep -n "PROTHEUS_PASS=.*[^=]" -- .env.example`

Expected: diff válido e nenhuma senha preenchida.

### Task 2: Registrar Financeiro Hoje no worker atual com TDD

**Files:**
- Modify: `tests/test_worker_scheduling.py`
- Modify: `worker.py`
- Modify: `core/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Escrever teste de regressão da imagem combinada**

Adicionar teste que habilita as duas flags e exige os eventos:

```python
def test_financeiro_hoje_and_medicao_coexist_in_scheduler(self):
    now = datetime(2026, 7, 31, 0, 0)
    with patch.object(worker, "FINANCEIRO_HOJE_SCHEDULE_ENABLED", True), patch.object(
        worker, "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED", True
    ), patch.dict(os.environ, _valid_medicao_env(), clear=False):
        events = dict(worker._scheduled_events(now))
    self.assertEqual(events["financeiro_medicao"].strftime("%H:%M"), "00:01")
    self.assertEqual(events["financeiro_hoje"].strftime("%H:%M"), "00:30")
```

- [ ] **Step 2: Executar o teste e confirmar RED**

Run: `.venv/Scripts/python -m unittest tests.test_worker_scheduling.WorkerSchedulingTests.test_financeiro_hoje_and_medicao_coexist_in_scheduler -v`

Expected: FAIL porque `worker` ainda não registra `financeiro_hoje`.

- [ ] **Step 3: Integrar configuração e agenda mínimas**

Adicionar ao worker os imports, root, signal, conversão de timezone, execução
manual/agendada e registro do evento. Preservar integralmente
`_financeiro_schedule_settings()` e o despacho de `financeiro_medicao`.

- [ ] **Step 4: Executar o teste e confirmar GREEN**

Run: `.venv/Scripts/python -m unittest tests.test_worker_scheduling.WorkerSchedulingTests.test_financeiro_hoje_and_medicao_coexist_in_scheduler -v`

Expected: PASS.

- [ ] **Step 5: Cobrir sinal, prioridade e avanço do slot**

Run: `.venv/Scripts/python -m pytest -q tests/test_worker_scheduling.py tests/test_financeiro_hoje_schedule.py tests/test_routerbox_coordination.py`

Expected: todos passam.

### Task 3: Adicionar defesa de composição da imagem

**Files:**
- Create: `scripts/smoke_automation_capabilities.py`
- Create: `tests/test_automation_capabilities.py`
- Modify: `Dockerfile`

- [ ] **Step 1: Escrever teste que reprova capacidade ausente**

```python
def test_enabled_capability_requires_importable_module(monkeypatch):
    monkeypatch.setenv("FINANCEIRO_HOJE_SCHEDULE_ENABLED", "true")
    with pytest.raises(CapabilityMissing):
        validate_capabilities(import_module=lambda name: (_ for _ in ()).throw(ImportError(name)))
```

- [ ] **Step 2: Confirmar RED**

Run: `.venv/Scripts/python -m pytest -q tests/test_automation_capabilities.py`

Expected: FAIL porque o smoke ainda não existe.

- [ ] **Step 3: Implementar validação por import**

O script deve mapear flags para módulos sem ler ou imprimir segredos:

```python
CAPABILITIES = {
    "FINANCEIRO_HOJE_SCHEDULE_ENABLED": "flows.financeiro_hoje.runner",
    "FINANCEIRO_MEDICAO_SCHEDULE_ENABLED": "flows.financeiro_medicao.runner",
}
```

- [ ] **Step 4: Confirmar GREEN e executar smoke real**

Run: `.venv/Scripts/python -m pytest -q tests/test_automation_capabilities.py && .venv/Scripts/python scripts/smoke_automation_capabilities.py`

Expected: PASS e saída sem segredos.

### Task 4: Validar, versionar e construir

**Files:**
- Modify: `docs/financeiro-hoje-runbook.md`
- Modify: `docs/superpowers/specs/2026-07-31-financeiro-combinado-design.md`

- [ ] **Step 1: Executar testes focados**

Run: `.venv/Scripts/python -m pytest -q tests/test_financeiro_hoje_*.py tests/test_routerbox_coordination.py tests/test_worker_scheduling.py tests/test_financeiro_medicao_schedule.py tests/test_automation_capabilities.py`

Expected: zero falhas.

- [ ] **Step 2: Executar a suíte combinada uma vez**

Run: `.venv/Scripts/python -m pytest -q`

Expected: zero falhas; skips de browser/plataforma documentados.

- [ ] **Step 3: Verificar diff e criar commit**

Run: `git diff --check && git status --short`

Expected: somente arquivos desta correção.

- [ ] **Step 4: Construir imagem imutável**

Run: `$revision = git rev-parse HEAD; $short = git rev-parse --short=10 HEAD; docker build --label "org.opencontainers.image.revision=$revision" -t "easypanel/apps/dmais_automacoes:financeiro-combinado-$short" .`

Expected: build concluído e smoke dos dois módulos aprovado.

### Task 5: Implantar e validar produção

**Files:**
- Runtime only: Docker service `apps_dmais_automacoes`

- [ ] **Step 1: Registrar imagem, mount e ponteiros atuais**

Run: `docker service inspect apps_dmais_automacoes` e leitura dos dois `current.json`.

Expected: mount `/srv/dmais/data_pipeline:/app/data_pipeline` preservado.

- [ ] **Step 2: Atualizar somente a imagem da automação**

Run: `docker service update --image easypanel/apps/dmais_automacoes:financeiro-combinado-$short apps_dmais_automacoes`

Expected: `1/1` estável e zero reinícios.

- [ ] **Step 3: Validar capacidades e próximos eventos**

Expected: startup registra Financeiro Hoje ativo e Medição ativa; próximos
slots correspondem a 27 horários do Hoje e 00:01 da Medição.

- [ ] **Step 4: Executar Financeiro Hoje real e verificar Django**

Expected: `success=true`, `stage=published`, LOGA e ACERTA no mesmo `run_id`, e
`pipeline_hoje.resolve()` abre o workbook publicado.

- [ ] **Step 5: Confirmar que a Medição permaneceu intacta**

Expected: hash do `current.json` e catálogo ativo iguais aos valores anteriores
ao deploy; próxima execução em 00:01.
