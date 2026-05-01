# 📊 Fluxo de Automação - TOTVS WebApp
**Processo:** Exportação de relatório "MAT ESTOQUE POR TÉCNICO"  
**Objetivo:** Automatizar geração de planilhas XLSX por técnico

---

## 🔐 1. Acesso ao Sistema

### [01] Acessar sistema
- **Ação:** Navegar para a URL do TOTVS WebApp  
- **Esperar:** Página inicial carregar completamente  

### [02] Confirmação inicial (opcional)
- **Ação:** Clicar no botão "OK"  
- **Condição:** Executar somente se o botão estiver visível  
- **Esperar:** Tela de login aparecer  

---

## 🔑 2. Login

### [03] Preencher usuário
- **Ação:** Preencher campo "Usuário"  
- **Dado:** Ler variável `USER` do arquivo `.env`  
- **Esperar:** Campo preenchido  

### [04] Preencher senha
- **Ação:** Preencher campo "Senha"  
- **Dado:** Ler variável `PASSWORD` do arquivo `.env`  
- **Esperar:** Campo preenchido  

### [05] Realizar login
- **Ação:** Clicar no botão "Entrar"  
- **Esperar:** Página inicial do sistema carregar  

### [06] Confirmação pós-login (opcional)
- **Ação:** Clicar em "Entrar" novamente  
- **Condição:** Apenas se houver segunda confirmação  

---

## 📂 3. Navegação

### [07] Abrir favoritos
- **Ação:** Clicar em "Favoritos"  
- **Local:** Menu lateral esquerdo  
- **Esperar:** Menu expandido  

### [08] Abrir relatório
- **Ação:** Clicar em "MAT ESTOQUE POR TECNICO"  
- **Esperar:** Nova tela carregada  

### [09] Confirmar tela
- **Ação:** Clicar em "Confirmar"  
- **Esperar:** Tela de filtro aberta  

### [10] Popup opcional
- **Ação:** Clicar em "Não exibir nos próximos 7 dias"  
- **Condição:** Apenas se popup aparecer  
- **Esperar:** Popup desaparecer  

---

## ⚙️ 4. Execução do Relatório

### [11] Inserir código do técnico
- **Ação:** Preencher campo "Armazém"  
- **Dado:** Ler primeiro código do arquivo `technicians.json`  
- **Esperar:** Campo preenchido  

### [12] Confirmar código
- **Ação:** Clicar em "OK"  
- **Esperar:** Tela de geração de relatório abrir  

---

## 📄 5. Configuração da Planilha

### [13] Selecionar aba planilha
- **Ação:** Clicar na aba "Planilha"  
- **Local:** Menu esquerdo do popup  
- **Esperar:** Conteúdo alterado  

### [14] Selecionar tipo de planilha
- **Ação:** Abrir dropdown "Tipo de Planilha"  
- **Esperar:** Opções exibidas  

### [15] Definir formato XLSX
- **Ação:** Selecionar "Formato de Tabela XLSX"  
- **Esperar:** Opção marcada  

---

## ⬇️ 6. Geração e Download

### [16] Gerar relatório
- **Ação:** Clicar em "Imprimir"  
- **Esperar:** Popup de confirmação aparecer  

### [17] Confirmar download
- **Ação:** Clicar em "Sim"  
- **Esperar:** Download iniciar  

---

## 🔁 7. Loop de Execução

### [18] Repetição
- **Ação:** Aguardar 7 segundos  
- **Ação:** Retornar ao passo [07]  
- **Objetivo:** Executar para múltiplos técnicos  

---
