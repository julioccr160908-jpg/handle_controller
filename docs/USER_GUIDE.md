# HandControl — Manual do Usuário e Guia de Uso

O **HandControl** é um aplicativo desktop para Windows que permite controlar o mouse, janelas e atalhos do sistema por meio de gestos da mão capturados pela webcam, operando de forma **100% offline, local e segura**, sem qualquer transmissão de dados ou dependência de internet.

---

## 1. Como Iniciar o Aplicativo

### Opção 1: Executável Standalone `.exe` (Sem necessidade de Python)
Após a compilação, o aplicativo está disponível em:
```
dist\HandControl\HandControl.exe
```
Dê dois cliques diretamente no arquivo `HandControl.exe` no Windows Explorer para abrir.

### Opção 2: Pelo Ambiente Python de Desenvolvimento
Abra o terminal PowerShell na pasta do projeto e execute:
```powershell
.venv\Scripts\python.exe -m handcontrol
```

---

## 2. Visão Geral da Interface

A tela principal do HandControl é organizada em 4 áreas centrais:

1. **Barra Superior:**
   - **Seletor de Perfis:** Alterna rapidamente entre perfis de uso (Padrão, Apresentação, Mídia, Jogos, etc.).
   - **Botão "⚙️ Editar Perfis":** Abre o Editor Visual de Perfis, regras, trajetórias e atalhos.
   - **Badge de Status de Segurança:** Mostra em destaque se o sistema está em `🔒 MODO SIMULAÇÃO` (verde) ou `⚡ MODO REAL ATIVO` (laranja).

2. **Área de Vídeo e Região Ativa:**
   - Exibe a imagem da webcam com as articulações da mão desenhadas em tempo real.
   - **Retângulo Verde (Região Ativa):** Mapeia a área da câmera para a tela do Windows. Mover a mão para os cantos deste retângulo atinge os 4 cantos da sua tela sem esforço.
   - **Borda Colorida de Segurança:**
     - Borda Verde: Câmera rodando em Simulação.
     - Borda Âmbar/Laranja: Comandos Reais ativos no Windows.
     - Borda Vermelha: Parada de Emergência acionada.

3. **Barra de Métricas em Tempo Real:**
   - **FPS:** Taxa de quadros de processamento (alvo 30 FPS).
   - **Latência:** Tempo decorrido entre a captura do quadro e o reconhecimento do gesto (< 30 ms).
   - **RAM:** Uso de memória física do processo em Megabytes.
   - **Mãos:** Mão(s) detectada(s) e grau de confiança.
   - **Gesto:** Gesto atualmente reconhecido.
   - **Ação:** Última ação disparada.

4. **Painel de Controle e Histórico:**
   - Botões de início: **▶ Iniciar**, **⏸ Pausar**, **⏹ Parar**.
   - Botão **⚡ Ativar Comandos Reais**: Libera o controle real do mouse no Windows (após confirmação).
   - Botão **🛑 Parada de Emergência**: Bloqueio total imediato.
   - Painel de Janelas: Lista as janelas ativas para seleção e manipulação multi-monitor.
   - Histórico de Ações: Log das ações recentes com status (Simulado, Executado, Recusado).

---

## 3. Segurança Inviolável (Proteção do seu Computador)

O HandControl foi projetado com múltiplas camadas de segurança para evitar que comandos acidentais afetem seu computador:

1. **Simulação por Padrão:** Ao abrir o programa, ele **sempre** inicia em modo de simulação. Nenhuma ação física é executada no Windows até você clicar explicitamente em *Ativar Comandos Reais*.
2. **Atalho de Emergência Global (`Ctrl + Shift + F12`):**
   - Funciona a qualquer instante, mesmo que o HandControl esteja minimizado ou em segundo plano.
   - Interrompe instantaneamente o envio de comandos, solta qualquer botão de mouse ou tecla presa e congela a operação.
3. **Congelamento por Perda da Mão:**
   - Se a sua mão sair do enquadramento da câmera ou a lente for coberta, o cursor para imediatamente no local exato e todos os botões/teclas são liberados (`release_all`), evitando seleções ou arrastes indesejados.
4. **Proteção de Janelas Elevadas (UIPI):**
   - Janelas pertencentes a processos como Administrador (PowerShell elevado, Gerenciador de Tarefas) são protegidas contra bloqueios de interface.

---

## 4. Guia de Gestos

### Controle de Mouse (Mão Direita ou Preferida)

| Gesto | Como Fazer | Ação no Windows |
|---|---|---|
| **Apontar (Point)** | Dedo indicador estendido para cima, demais dedos dobrados | Move o cursor pela tela com filtro anti-tremor |
| **Embreagem (Clutch)** | Abra a palma ou feche o punho | O cursor para no lugar exato sem tremer (drift) |
| **Pinça Indicador (Pinch Index)** | Junte a ponta do polegar com a ponta do indicador | **Clique Esquerdo** (cursor congela no toque) |
| **Pinça Média (Pinch Middle)** | Junte a ponta do polegar com a ponta do dedo médio | **Clique Direito** (abre menu de contexto) |
| **Arraste (Pinch Sustained)** | Mantenha a pinça fechada por mais de 400 ms e mova a mão | **Arrastar e Soltar** (separe os dedos para soltar) |
| **Rolagem (Two Fingers)** | Indicador e médio estendidos juntos apontando para cima | Deslize a mão para cima/baixo para rolar a página |

### Gestos Dinâmicos e Atalhos

| Gesto | Como Fazer | Ação Padrão |
|---|---|---|
| **Swipe Esquerda / Direita** | Movimento rápido e retilíneo da mão na horizontal | Avançar ou Retroceder slide / página |
| **Círculo Horário (Circle CW)** | Desenhar um círculo no ar no sentido horário | Aumentar Volume do Sistema |
| **Círculo Anti-horário (Circle CCW)** | Desenhar um círculo no ar no sentido anti-horário | Diminuir Volume do Sistema |
| **Duas Palmas Abertas** | Mostrar as duas mãos abertas para a câmera | Pausar / Despausar Comandos |

### Controle de Janelas e Multi-monitor (Mão Esquerda)

| Gesto / Ação | Como Fazer | Efeito |
|---|---|---|
| **Agarrar Janela (Fist Grab)** | Feche o punho esquerdo | Agarra a janela em foco **sem nenhum salto ou teletransporte** |
| **Mover Janela** | Mova a mão esquerda com o punho fechado | A janela segue o movimento suavemente |
| **Soltar Janela** | Abra a mão esquerda | A janela permanece na nova posição |
| **Próximo Monitor** | Clique no botão no painel de janelas | Transfere a janela em foco para o monitor seguinte |

---

## 5. Editor de Perfis e Customização

Clique em **"⚙️ Editar Perfis"** na barra superior para:

- **Criar e Duplicar Perfis:** Configure perfis específicos para Navegação, Jogos, Apresentações (PowerPoint) ou Reprodutor de Mídia.
- **Personalizar Regras:** Escolha o gesto gatilho, a mão correspondente, a ação desejada (clique, tecla, atalho, comando de sistema) e parâmetros.
- **Gravação de Trajetória Personalizada (DTW):**
  1. Crie uma regra com o gatilho `custom_trajectory`.
  2. Clique em **"🔴 Gravar Trajetória"**.
  3. Desenhe um símbolo no ar diante da câmera (ex: a letra "Z" ou "V").
  4. O algoritmo Dynamic Time Warping salvará a trajetória e a reconhecerá independentemente da velocidade do movimento!
- **Detecção Automática de Conflitos:** O editor analisa as regras em tempo real e emite alertas visuais se dois comandos conflitantes tiverem a mesma prioridade.
- **Importar e Exportar JSON:** Salve suas configurações em arquivos `.json` ou compartilhe com outros computadores. Arquivos inválidos são recusados com segurança.

---

## 6. Bandeja do Sistema (System Tray) e Privacidade

- **Minimização Silenciosa:** Ao clicar em minimizar (`_`) ou fechar (`X`), o HandControl continua executando em segundo plano na área de notificação do Windows (ao lado do relógio).
- **Indicador Visual de Privacidade:**
  - 🟢 **S (Verde):** Câmera ativa em Modo de Simulação.
  - 🟠 **R (Laranja):** Câmera ativa em MODO REAL monitorando comandos.
  - 🟡 **P (Amarelo):** Rastreamento pausado.
  - ⚪ **X (Cinza):** Câmera parada.
- **Restauração:** Dê dois cliques no ícone da bandeja para reabrir a janela principal.

---

## 7. Perguntas Frequentes e Resolução de Problemas

### 1. A câmera não inicia ou exibe erro
- Verifique se outro aplicativo (Teams, Zoom, Discord) não está usando a câmera com exclusividade.
- Clique em **⏹ Parar** e depois em **▶ Iniciar** para restabelecer a conexão DirectShow.

### 2. O cursor está muito sensível ou tremendo
- Abra o painel **📐 Região Ativa** e ajuste as margens para adequar ao seu espaço de movimento.
- O filtro 1 € já suaviza microtremores automaticamente sem adicionar atraso.

### 3. O atalho de emergência foi acionado ("🔴 EMERGÊNCIA")
- O sistema bloqueou todos os comandos por segurança.
- Para voltar a operar normalmente, clique no botão verde **"✅ Retomar (limpar emergência)"** na janela principal.

### 4. Minhas configurações foram perdidas ou corrompidas?
- O HandControl grava suas configurações de forma atômica em `%APPDATA%\HandControl\config.json` e mantém um arquivo `.bak` automático. Se o arquivo for corrompido, a recuperação é realizada automaticamente sem travar o aplicativo.
