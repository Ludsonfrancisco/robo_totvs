# Multiplica Loga Auth Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Renovar automaticamente a sessão da Loga com segredos do EasyPanel quando a sessão persistida do Multiplica expirar.

**Architecture:** `authenticated_page` continua responsável pelo ciclo de vida do navegador e delega o login de fallback a funções pequenas no mesmo módulo. O contexto usa o estado persistido quando disponível, autentica somente diante da tela real de login e grava o novo estado de forma atômica antes de entregar a página ao coletor.

**Tech Stack:** Python 3, Playwright síncrono, unittest/pytest, Docker Swarm e EasyPanel.

## Global Constraints

- Nunca registrar usuário, senha, cookies ou conteúdo do estado autenticado.
- Nunca incluir credenciais em argumentos de linha de comando ou commits.
- Manter Protheus e o agendamento Multiplica desligados durante o dry run.
- Não alterar código, configuração, arquivos ou agendamento do RouterBox.
- Não excluir nem mover banco, volumes, pacotes ou dados existentes.
- Executar somente testes focados desta alteração.

---

### Task 1: Corrigir o bootstrap manual para abas abertas pelo login

**Files:**
- Modify: `flows/multiplica/bootstrap_auth.py`
- Test: `tests/test_multiplica_bootstrap_auth.py`

**Interfaces:**
- Consumes: `context.pages` do Playwright.
- Produces: `_find_authenticated_page(context) -> Page | None`.

- [x] **Step 1: Manter o teste falhando para uma segunda aba autenticada**

```python
class _LoginPage(_Page):
    def title(self):
        return "Dashboard - Loga Internet"

    def locator(self, selector):
        return _Locator(1 if selector == 'input[type="password"]' else 0)


def test_finds_authenticated_page_opened_after_login(self):
    context = _Context()
    authenticated_page = _Page()
    context.page = _LoginPage()
    context.pages = [context.page, authenticated_page]
    self.assertIs(
        bootstrap_auth._find_authenticated_page(context),
        authenticated_page,
    )
```

- [x] **Step 2: Confirmar a falha**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_multiplica_bootstrap_auth.py -q
```

Expected: uma falha informando que `_find_authenticated_page` não existe.

- [x] **Step 3: Selecionar qualquer aba autenticada do contexto**

```python
def _find_authenticated_page(context):
    for page in reversed(context.pages):
        if _is_authenticated_indicators_page(page):
            return page
    return None
```

Em `main`, substituir a validação da página inicial por:

```python
if _find_authenticated_page(context) is None:
    context.close()
    browser.close()
    raise RuntimeError("AUTH_MARKER_NOT_FOUND")
```

- [x] **Step 4: Confirmar o teste verde**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_multiplica_bootstrap_auth.py -q
```

Expected: `2 passed`.

- [x] **Step 5: Commit**

```powershell
git add flows/multiplica/bootstrap_auth.py tests/test_multiplica_bootstrap_auth.py
git commit -m "fix(multiplica): aceitar aba autenticada no bootstrap"
```

### Task 2: Implementar fallback automático sem expor segredos

**Files:**
- Modify: `flows/multiplica/browser.py`
- Modify: `.env.example`
- Create: `tests/test_multiplica_browser.py`

**Interfaces:**
- Consumes: `MULTIPLICA_LOGA_USER`, `MULTIPLICA_LOGA_PASSWORD`, `Settings.loga_url` e `Settings.storage_state_path`.
- Produces: `_ensure_authenticated(page, context, settings, environ) -> None`.

- [x] **Step 1: Criar dublês e testes de autenticação**

Os testes devem verificar:

```python
def test_valid_session_does_not_read_or_fill_credentials():
    page = FakePage(title="Indicadores SLA e Qualidade", password_inputs=0)
    _ensure_authenticated(page, FakeContext(), settings, {})
    assert page.fills == []


def test_expired_session_logs_in_once_and_saves_state_atomically():
    page = FakeLoginPage()
    context = FakeContext(page)
    _ensure_authenticated(
        page,
        context,
        settings,
        {
            "MULTIPLICA_LOGA_USER": "gestor@example.invalid",
            "MULTIPLICA_LOGA_PASSWORD": "segredo-de-teste",
        },
    )
    assert page.fills == [
        ("E-Mail", "gestor@example.invalid"),
        ("Senha", "segredo-de-teste"),
    ]
    assert page.login_clicks == 1
    assert context.saved_paths == [
        settings.runtime_root / "runtime" / "loga-storage-state.json.tmp"
    ]
    assert settings.storage_state_path.is_file()


def test_missing_credentials_preserves_auth_expired():
    page = FakeLoginPage()
    with pytest.raises(CollectionError, match="AUTH_EXPIRED"):
        _ensure_authenticated(page, FakeContext(page), settings, {})


def test_rejected_credentials_preserves_auth_expired():
    page = FakeLoginPage(authenticates=False)
    with pytest.raises(CollectionError, match="AUTH_EXPIRED"):
        _ensure_authenticated(
            page,
            FakeContext(page),
            settings,
            {
                "MULTIPLICA_LOGA_USER": "gestor@example.invalid",
                "MULTIPLICA_LOGA_PASSWORD": "senha-invalida",
            },
        )
```

- [x] **Step 2: Confirmar que os testes falham**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_multiplica_browser.py -q
```

Expected: falha de importação de `_ensure_authenticated`.

- [x] **Step 3: Implementar autenticação condicional**

Adicionar a `flows/multiplica/browser.py`:

```python
import os

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def _is_authenticated(page):
    return (
        page.title().strip() == "Indicadores SLA e Qualidade"
        and page.locator('input[type="password"]').count() == 0
    )


def _ensure_authenticated(page, context, settings, environ):
    page.goto(settings.loga_url, wait_until="networkidle")
    if _is_authenticated(page):
        return

    password_input = page.get_by_label("Senha", exact=True)
    if password_input.count() != 1:
        raise CollectionError("AUTH_EXPIRED")

    username = str(environ.get("MULTIPLICA_LOGA_USER", "")).strip()
    password = str(environ.get("MULTIPLICA_LOGA_PASSWORD", ""))
    if not username or not password:
        raise CollectionError("AUTH_EXPIRED")

    page.get_by_label("E-Mail", exact=True).fill(username)
    password_input.fill(password)
    page.get_by_role("button", name="Entrar", exact=True).click()
    try:
        page.locator('input[type="password"]').wait_for(
            state="detached",
            timeout=30_000,
        )
        page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError as exc:
        raise CollectionError("AUTH_EXPIRED") from exc
    if not _is_authenticated(page):
        raise CollectionError("AUTH_EXPIRED")

    temporary = settings.runtime_root / "runtime" / "loga-storage-state.json.tmp"
    context.storage_state(path=str(temporary))
    os.chmod(temporary, 0o600)
    os.replace(temporary, settings.storage_state_path)
    os.chmod(settings.storage_state_path, 0o600)
```

Alterar o contexto para aceitar estado ausente:

```python
context_options = {}
if settings.storage_state_path.is_file():
    context_options["storage_state"] = str(settings.storage_state_path)
context = browser.new_context(**context_options)
page = context.new_page()
_ensure_authenticated(page, context, settings, os.environ)
yield page
```

Adicionar a `.env.example`, sem valores:

```dotenv
MULTIPLICA_LOGA_USER=
MULTIPLICA_LOGA_PASSWORD=
```

- [x] **Step 4: Executar testes focados**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_multiplica_browser.py tests\test_multiplica_bootstrap_auth.py tests\test_multiplica_loga.py tests\test_multiplica_runner.py -q
```

Expected: todos passam e nenhuma saída contém os valores de teste das credenciais.

- [x] **Step 5: Verificar isolamento RouterBox e commit**

Run:

```powershell
git diff --check
git diff --exit-code d45ed8e -- flows/routerbox_backlog worker.py
git add .env.example flows/multiplica/browser.py tests/test_multiplica_browser.py
git commit -m "feat(multiplica): renovar autenticação Loga por segredo"
```

Expected: `git diff --check` sem saída e nenhum diff RouterBox.

### Task 3: Implantar e validar um dry run controlado

**Files:**
- Modify: `docs/superpowers/evidence/2026-07-23-multiplica-loga-auth-fallback.md`

**Interfaces:**
- Consumes: imagem do commit da Task 2 e segredos inseridos pelo usuário no EasyPanel.
- Produces: serviço `apps_dmais_automacoes` em `1/1` e `run_multiplica.done` sanitizado.

- [x] **Step 1: Push da branch**

```powershell
git push origin codex/dmais-automacoes
```

Expected: `origin/codex/dmais-automacoes` no SHA da Task 2.

- [x] **Step 2: Configurar segredos no EasyPanel**

Adicionar somente pelo formulário de ambiente do serviço:

```text
MULTIPLICA_LOGA_USER=<valor informado pelo usuário>
MULTIPLICA_LOGA_PASSWORD=<valor informado pelo usuário>
```

Não exibir os valores em terminal, logs, capturas ou documentação.

- [x] **Step 3: Implantar e preservar flags**

Confirmar após a implantação:

```text
PROTHEUS_ENABLED=false
ROUTERBOX_HOURLY_ENABLED=true
MULTIPLICA_SCHEDULE_ENABLED=false
```

Fixar a imagem no SHA implantado e manter a revisão anterior disponível para
rollback.

- [x] **Step 4: Disparar uma execução manual**

No container ativo:

```sh
touch /app/data_pipeline/multiplica/multiplica.signal
```

Expected: novo `run_multiplica.done` com `success=true`, `error_code=""` e ao
menos um `bundle_id`.

- [x] **Step 5: Registrar evidência sanitizada**

Criar o arquivo de evidência contendo somente:

```markdown
- SHA implantado
- horário inicial e final
- `success` e `error_code`
- quantidade de bundles e ciclos
- flags de segurança
- modo e proprietário do arquivo de sessão, sem seu conteúdo
- confirmação de que RouterBox permaneceu inalterado
```

- [x] **Step 6: Commit e push da evidência**

```powershell
git add docs/superpowers/evidence/2026-07-23-multiplica-loga-auth-fallback.md
git commit -m "docs(multiplica): registrar dry run autenticado"
git push origin codex/dmais-automacoes
```
