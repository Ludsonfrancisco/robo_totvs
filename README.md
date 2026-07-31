# Dmais Automações

Aplicação generalista em Python + Playwright para as automações operacionais da
Dmais. O repositório mantém o nome legado `robo_totvs`, mas o app alvo no
EasyPanel é `dmais_automacoes` e o serviço esperado é
`apps_dmais_automacoes`.

O Protheus/TOTVS foi descontinuado e permanece desabilitado por padrão. O
RouterBox existente deve continuar sem alteração de código, horário ou contrato.
O Multiplica usa sessão própria e execução inicialmente manual; a agenda futura
das 23h50 permanece desabilitada. A medição financeira também permanece
desabilitada por padrão e, quando habilitada, executa diariamente às 00:01 no
fuso `America/Sao_Paulo`.

O container roda no EasyPanel ao lado do Portal D+
([dmais_portal](https://github.com/Ludsonfrancisco/dmais_portal)) e troca
arquivos por um bind compartilhado.

Spec completa: ver [`PRD.md`](./PRD.md). Roteiro de desenvolvimento: ver [`TASKS.md`](./TASKS.md).

## Modos de execução

| Modo | Trigger | Comando equivalente |
|------|---------|---------------------|
| **CLI** (dev/debug) | `python main.py` no terminal | flags livres: `--limite`, `--reset`, `--retry-falhos`, `--incluir-desligados` |
| **Worker scheduler** (produção) | `python worker.py` (em loop) | dispara automaticamente em `ROBOT_SCHEDULE_HOUR:MINUTE` (default 06:00) |
| **Worker signal-driven** (produção) | Portal D+ cria `run.signal` no volume | worker detecta em ≤5s e roda `main.main(["--retry-falhos"])` |
| **Multiplica manual** | criar `multiplica/multiplica.signal` no volume | coleta o pacote Loga sem habilitar a agenda |
| **Medição financeira** | agenda opcional do worker | coleta diária às 00:01 no fuso configurado |

O worker coordena automações independentes. `PROTHEUS_ENABLED=false` impede a
rotina obsoleta; RouterBox mantém suas flags atuais e o sinal manual do
Multiplica funciona com `MULTIPLICA_SCHEDULE_ENABLED=false`.

## Requisitos

- Python 3.11+
- Linux/macOS/Windows
- Tesseract OCR instalado no sistema (opcional, para validação defensiva)

## Instalação Rápida (Linux/macOS)

```bash
chmod +x install.sh
./install.sh
```

O script criará o ambiente virtual, instalará as dependências e o browser necessário.

## Setup Manual

```bash
# 1. Criar e ativar venv
python3 -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Instalar browser do Playwright
playwright install chromium

# 4. Configurar .env
cp .env.example .env
#   editar .env com PROTHEUS_URL, PROTHEUS_USER, PROTHEUS_PASS reais
```

## Uso

### Modo CLI (dev/debug)

```bash
# Execução padrão (processa apenas técnicos "Ativo" e pula sucessos do dia)
python main.py

# Processar todos (incluindo desligados) e reiniciar do zero (ignorar checkpoint)
python main.py --incluir-desligados --reset

# Reprocessar apenas os que falharam na última rodada
python main.py --retry-falhos

# Testar com apenas os 3 primeiros da lista
python main.py --limite 3
```

### Modo Worker (produção em container)

```bash
# Inicia o loop persistente — scheduler + signal-driven
python worker.py
```

O `worker.py` é o `CMD` default do Dockerfile. Configurável via envs:

| Variável | Default | Função |
|----------|---------|--------|
| `DATA_PIPELINE_DIR` | `/app/data_pipeline` | Volume compartilhado com Portal D+ |
| `PROTHEUS_ENABLED` | `false` | Mantém a automação obsoleta desabilitada; `true` somente em rollback controlado |
| `ROUTERBOX_HOURLY_ENABLED` | `true` | Mantém o agendamento RouterBox existente |
| `ROBOT_SCHEDULE_HOUR` | `6` | Hora do run diário |
| `ROBOT_SCHEDULE_MINUTE` | `0` | Minuto do run diário |
| `ROBOT_RUN_ON_START` | `false` | `true` força run imediato ao subir o container |
| `ROBOT_INCLUDE_DISMISSED` | `false` | `true` adiciona `--incluir-desligados` ao run agendado |
| `WORKER_POLL_INTERVAL` | `5` | Segundos do loop de detecção de `run.signal` |
| `MULTIPLICA_SCHEDULE_ENABLED` | `false` | Habilita a agenda diária do Multiplica; permanece desligada inicialmente |
| `MULTIPLICA_SCHEDULE_HOUR` | `23` | Hora futura da coleta diária |
| `MULTIPLICA_SCHEDULE_MINUTE` | `50` | Minuto futuro da coleta diária |
| `MULTIPLICA_TIMEZONE` | `America/Sao_Paulo` | Fuso do agendamento futuro |
| `MULTIPLICA_RUNTIME_ROOT` | `/app/data_pipeline/multiplica` | Sessão, inbox e runtime próprios |
| `MULTIPLICA_LOGA_URL` | sem default | URL HTTPS da Loga, sem credenciais |
| `FINANCEIRO_MEDICAO_SCHEDULE_ENABLED` | `false` | Habilita explicitamente a coleta diária de medição |
| `FINANCEIRO_MEDICAO_SCHEDULE_HOUR` | `0` | Hora local da coleta de medição |
| `FINANCEIRO_MEDICAO_SCHEDULE_MINUTE` | `1` | Minuto local da coleta de medição |
| `FINANCEIRO_MEDICAO_TIMEZONE` | `America/Sao_Paulo` | Fuso usado pelo scheduler da medição |
| `FINANCEIRO_MEDICAO_LOGA_URL` | sem default | URL HTTPS do dashboard Loga, sem credenciais |
| `FINANCEIRO_MEDICAO_RUNTIME_ROOT` | `/app/data_pipeline/financeiro_medicao` | Raiz persistente exclusiva da medição |
| `FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS` | `1200` | Espera máxima pelos locks do fluxo e do Chromium |
| `FINANCEIRO_MEDICAO_RETRY_BASE_SECONDS` | `60` | Espera inicial entre tentativas transitórias do scheduler |
| `FINANCEIRO_MEDICAO_RETRY_MAX_SECONDS` | `900` | Limite do backoff exponencial do scheduler |
| `LOGA_DASHBOARD_USER_FILE` | vazio | Arquivo de secret do usuário; tem precedência sobre envs |
| `LOGA_DASHBOARD_PASSWORD_FILE` | vazio | Arquivo de secret da senha; tem precedência sobre envs |
| `LOGA_DASHBOARD_USER` | vazio | Usuário direto, usado quando `*_USER_FILE` está vazio |
| `LOGA_DASHBOARD_PASSWORD` | vazio | Senha direta, usada quando `*_PASSWORD_FILE` está vazio |

Se as credenciais `LOGA_DASHBOARD_*` não forem informadas, o fluxo reutiliza
`MULTIPLICA_LOGA_USER` e `MULTIPLICA_LOGA_PASSWORD` como último fallback.
Arquivos de secret são preferíveis às variáveis diretas e nunca devem ser
versionados.

### Contrato de arquivos no volume compartilhado

```
DATA_PIPELINE_DIR/
├── entrada/<YYYY-MM-DD>/*.xlsx   # XLSX baixados pelo robô
├── processos/                    # snapshots arquivados pelo Portal D+
├── run.signal     # Portal D+ CRIA → worker CONSOME    (pedido de retry)
├── run.log        # worker ESCREVE                      (sink loguru ao vivo)
├── run.done       # worker CRIA ao final                (JSON com resultado)
├── signal.ready   # worker CRIA se ok > 0               (flag de pendência)
├── multiplica/
    ├── multiplica.signal     # acionamento manual; worker consome
    └── run_multiplica.done   # resultado sanitizado da coleta
└── financeiro_medicao/
    ├── done.json                     # último resultado sanitizado do fluxo
    ├── published/                    # bundles publicados atomicamente
    └── runtime/
        ├── schedule.signal.json      # tentativa pendente e próximo retry
        └── schedule-watermark.json   # dia concluído com success ou terminal
```

O sinal manual do Multiplica é aceito mesmo com
`MULTIPLICA_SCHEDULE_ENABLED=false`. A agenda de 23h50 só entra no
supervisor quando essa variável for explicitamente habilitada.

### Autenticação assistida do Multiplica

Em uma máquina com interface gráfica e runtime persistente:

```bash
export MULTIPLICA_LOGA_URL='https://ENDERECO-HTTPS-DA-LOGA'
export MULTIPLICA_RUNTIME_ROOT='/CAMINHO-PERSISTENTE/multiplica'
python -m flows.multiplica.bootstrap_auth
```

O login é concluído por uma pessoa no Chrome visível. Usuário e senha não são
armazenados no código ou em variáveis. O arquivo
`auth/loga-storage-state.json` deve ser transferido por canal seguro para o
runtime persistente do serviço, com ACL restrita, e nunca entrar no Git.

### Ativação segura da medição financeira

A agenda é opt-in. Ela faz catch-up do dia atual após 00:01 quando não existe
sucesso nem resultado terminal persistido, inclusive depois de reinício do
worker. Falhas transitórias são reprogramadas com backoff; falhas permanentes
encerram o evento do dia para evitar loop quente.

1. Mantenha `FINANCEIRO_MEDICAO_SCHEDULE_ENABLED=false`.
2. Configure a URL HTTPS, o runtime persistente, fuso, horário e espera de lock.
3. Injete usuário e senha por `LOGA_DASHBOARD_*_FILE` sempre que o ambiente
   oferecer secrets montados; use as variáveis diretas apenas como fallback.
4. Faça uma execução controlada com
   `python -m flows.financeiro_medicao.runner` e confira somente os campos
   sanitizados de `financeiro_medicao/done.json`.
5. Habilite `FINANCEIRO_MEDICAO_SCHEDULE_ENABLED=true` e reinicie um único
   worker responsável por essa agenda.

Exemplo sem valores reais:

```text
FINANCEIRO_MEDICAO_LOGA_URL=https://ENDERECO-HTTPS-DA-LOGA
FINANCEIRO_MEDICAO_RUNTIME_ROOT=/app/data_pipeline/financeiro_medicao
LOGA_DASHBOARD_USER_FILE=/run/secrets/loga_dashboard_user
LOGA_DASHBOARD_PASSWORD_FILE=/run/secrets/loga_dashboard_password
FINANCEIRO_MEDICAO_SCHEDULE_ENABLED=false
```

Para rollback, volte `FINANCEIRO_MEDICAO_SCHEDULE_ENABLED=false` e reinicie o
worker. Não apague `done.json`, `schedule.signal.json`, o watermark, bundles ou
o runtime: esses artefatos preservam auditoria, idempotência e uma tentativa
transitória ainda pendente. Logs e status expõem apenas códigos de erro; não
inclua URLs autenticadas, usuários, senhas ou tokens neles.

`run.done` payload:

```json
{
  "success": true,
  "message": "Todos os técnicos processados com sucesso.",
  "started_at": "2026-05-16T09:00:00Z",
  "finished_at": "2026-05-16T09:12:34Z",
  "exit_code": 0,
  "mode": "scheduled",
  "tecnicos_total": 33,
  "tecnicos_ok": 32,
  "tecnicos_falhos": [
    {"code": "HK", "name": "ALEXANDRE M.", "erro_msg": "Timeout", "tentativas": 3}
  ]
}
```

## Troubleshooting (Resolução de Problemas)

### 1. O robô não clica nos elementos (Canvas)
- **Causa:** O layout do Protheus mudou ou a resolução/zoom está diferente.
- **Solução:** Verifique se o `HEADLESS` no `.env` está condizente. Tente rodar com `HEADLESS=false` para ver o que está acontecendo. Se o layout mudou, as imagens em `referencias/` precisam ser atualizadas.

### 2. Timeout no download
- **Causa:** O sistema Protheus está lento para gerar o relatório.
- **Solução:** Aumente o `DOWNLOAD_TIMEOUT_S` no arquivo `.env` (padrão é 60s).

### 3. Erro de OCR (Tesseract)
- **Causa:** Tesseract não está no PATH do sistema.
- **Solução:** No Linux: `sudo apt install tesseract-ocr`. No Windows: Instale via instalador oficial e adicione ao PATH. O robô continua funcionando sem OCR, apenas pula a validação de nome.

## FAQ (Perguntas Frequentes)

**Q: Como altero a lista de técnicos?**  
A: Edite o arquivo `technicians.json` na raiz do projeto. Certifique-se de manter o formato JSON válido.

**Q: Onde ficam os arquivos baixados?**
A: Configurável via env `DOWNLOAD_DIR`. Em produção: `/app/data_pipeline/entrada/AAAA-MM-DD/`. Em dev local sem env setada: `~/Documentos/projects/data_pipeline/robo_totvs/entrada/AAAA-MM-DD/` (fallback legado).

**Q: Como vejo o que deu errado?**  
A: Confira os logs em `logs/run-*.log` e as capturas de tela das falhas em `logs/evidencias/`.

## Estrutura do Projeto

- `core/`: Primitivas de baixo nível (navegador, visão, config).
- `flows/`: Orquestração de alto nível (loop de processamento).
- `referencias/`: Imagens de referência para o sistema de visão.
- `downloads/`: Saída legada (dev local sem `DOWNLOAD_DIR` setado).
- `logs/`: Histórico de execução e evidências de erro.
- `state/`: Checkpoints para garantir idempotência (`checkpoint_<YYYY-MM-DD>.json`).
- `main.py`: Entrypoint CLI.
- `worker.py`: Loop persistente pra container (scheduler + signal-driven).
- `Dockerfile`: Build baseado em `mcr.microsoft.com/playwright/python:v1.49.0-jammy` + Chrome real.

## Deploy no EasyPanel

O serviço alvo é `apps_dmais_automacoes`, ao lado do `apps_dmais`. O serviço
legado `apps_robo_totvs` deve permanecer disponível, parado e não excluído para
rollback. Mounts:

| Tipo | Origem | Destino | Função |
|------|--------|---------|--------|
| Bind Mount | `/srv/dmais/data_pipeline` (host) | `/app/data_pipeline` | Runtime RouterBox e Multiplica |

Configuração inicial obrigatória:

```text
PROTHEUS_ENABLED=false
MULTIPLICA_SCHEDULE_ENABLED=false
MULTIPLICA_SCHEDULE_HOUR=23
MULTIPLICA_SCHEDULE_MINUTE=50
MULTIPLICA_TIMEZONE=America/Sao_Paulo
FINANCEIRO_MEDICAO_SCHEDULE_ENABLED=false
FINANCEIRO_MEDICAO_SCHEDULE_HOUR=0
FINANCEIRO_MEDICAO_SCHEDULE_MINUTE=1
FINANCEIRO_MEDICAO_TIMEZONE=America/Sao_Paulo
FINANCEIRO_MEDICAO_LOGA_URL=https://ENDERECO-HTTPS-DA-LOGA
FINANCEIRO_MEDICAO_RUNTIME_ROOT=/app/data_pipeline/financeiro_medicao
FINANCEIRO_MEDICAO_LOCK_WAIT_SECONDS=1200
LOGA_DASHBOARD_USER_FILE=/run/secrets/loga_dashboard_user
LOGA_DASHBOARD_PASSWORD_FILE=/run/secrets/loga_dashboard_password
```

Durante o corte, o RouterBox automático deve existir em apenas um serviço.
Criação, escala ou parada exige autorização explícita. Nunca remover serviços,
volumes ou arquivos durante o rollback.
