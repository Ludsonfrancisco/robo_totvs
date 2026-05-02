# robo-totvs

Robô em Python + Playwright que automatiza no TOTVS Protheus WebApp o download em XLSX do relatório **"Material em Estoque por Técnico"** para uma lista de técnicos definida em JSON.

Spec completa: ver [`PRD.md`](./PRD.md). Roteiro de desenvolvimento: ver [`TASKS.md`](./TASKS.md).

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
A: Na pasta `downloads/AAAA-MM-DD/`, organizados por data de execução.

**Q: Como vejo o que deu errado?**  
A: Confira os logs em `logs/run-*.log` e as capturas de tela das falhas em `logs/evidencias/`.

## Estrutura do Projeto

- `core/`: Primitivas de baixo nível (navegador, visão, config).
- `flows/`: Orquestração de alto nível (loop de processamento).
- `referencias/`: Imagens de referência para o sistema de visão.
- `downloads/`: Saída dos relatórios XLSX.
- `logs/`: Histórico de execução e evidências de erro.
- `state/`: Checkpoints para garantir idempotência.
