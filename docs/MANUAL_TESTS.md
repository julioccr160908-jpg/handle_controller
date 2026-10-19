# HandControl — Guia de Testes Manuais (Fase 4: Mouse Real)

Este documento contém o checklist de testes manuais guiados para validação do controle de mouse no Windows, conforme os critérios de aceitação da Fase 4 da Especificação Mestra.

---

## ⚠️ Instruções de Segurança Prévias

Antes de iniciar qualquer teste com **Modo Real** ativado:
1. **Atalho de Emergência Global:** `Ctrl + Shift + F12` interrompe imediatamente todos os comandos e solta qualquer botão/tecla presa, mesmo que a janela do HandControl não esteja em foco.
2. **Parada por Perda de Rastreamento:** Tirar a mão do campo de visão da câmera congela o cursor e solta os botões instantaneamente.
3. **Botão de Emergência na Interface:** O botão vermelho **🛑 Parada de Emergência** está sempre visível na barra inferior.

---

## Roteiro de Testes

### 1. Indicador Visual e Modo de Simulação vs Modo Real
- [ ] **Passo 1.1:** Inicie o aplicativo com `python -m handcontrol.main`.
- [ ] **Passo 1.2:** Verifique que o app inicia por padrão em **🔒 MODO SIMULAÇÃO** (badge verde).
- [ ] **Passo 1.3:** Clique em **▶ Iniciar**. A borda do vídeo deve ficar **verde**.
- [ ] **Passo 1.4:** Clique em **⚡ Ativar Comandos Reais**.
  - Deve surgir um diálogo modal de aviso e confirmação detalhando os atalhos de emergência.
  - Ao clicar em "Não", o app permanece em simulação.
  - Ao clicar em "Sim", o badge muda para **⚡ MODO REAL ATIVO** (laranja/âmbar) e a borda do vídeo muda para âmbar.

---

### 2. Região Ativa e Alcance dos 4 Cantos da Tela
- [ ] **Passo 2.1:** Observe o retângulo verde com cantoneiras desenhado sobre o vídeo da câmera. Esta é a **Região Ativa**.
- [ ] **Passo 2.2:** Faça a pose de apontar (indicador estendido, demais dedos dobrados) com a mão direita.
- [ ] **Passo 2.3:** Mova o dedo indicador para o canto superior esquerdo da região ativa na câmera.
  - *Resultado esperado:* O cursor do Windows deve alcançar o canto superior esquerdo da tela (0, 0).
- [ ] **Passo 2.4:** Mova para o canto superior direito da região ativa.
  - *Resultado esperado:* O cursor deve alcançar o canto superior direito da tela.
- [ ] **Passo 2.5:** Mova para os cantos inferiores esquerdo e direito.
  - *Resultado esperado:* O cursor alcança a barra de tarefas e os cantos inferiores da tela sem exigir que a mão saia do enquadramento.
- [ ] **Passo 2.6:** Clique em **📐 Região Ativa** e teste ajustar o slider de margens (5%, 10%, 20%). Verifique que a caixa na tela se ajusta em tempo real.

---

### 3. Embreagem (Clutch) e Congelamento ao Perder a Pose
- [ ] **Passo 3.1:** Aponte para a tela com o indicador estendido. O cursor se move acompanhando o dedo.
- [ ] **Passo 3.2:** Abra a palma da mão completamente ou feche o punho.
  - *Resultado esperado:* O cursor para instantaneamente no lugar exato onde estava, sem tremores nem deriva (*drift*).
- [ ] **Passo 3.3:** Mova a mão aberta para outro lado da câmera e volte a apontar.
  - *Resultado esperado:* O cursor retoma o movimento a partir de onde estava, sem "teleportar" bruscamente.

---

### 4. Clique Esquerdo sem Deslocamento do Cursor
- [ ] **Passo 4.1:** Posicione o cursor sobre um ícone na área de trabalho ou botão da interface.
- [ ] **Passo 4.2:** Faça o gesto de **Pinça Indicador** (junte a ponta do polegar com a ponta do indicador).
  - *Resultado esperado:* O cursor congela no instante do contato, impedindo que o tremor do clique desloque o ponteiro fora do alvo.
- [ ] **Passo 4.3:** O clique esquerdo é disparado (o ícone ou botão é clicado).
- [ ] **Passo 4.4:** Mantenha a pinça fechada.
  - *Resultado esperado:* Ocorre exatamente 1 clique (sem repetição contínua descontrolada).

---

### 5. Clique Direito
- [ ] **Passo 5.1:** Posicione o cursor em uma área vazia da área de trabalho ou em um arquivo.
- [ ] **Passo 5.2:** Faça a **Pinça Médio** (junte a ponta do polegar com a ponta do dedo médio).
  - *Resultado esperado:* O menu de contexto do Windows se abre na posição do cursor.

---

### 6. Arrastar e Soltar (Pinça Sustentada > 400 ms)
- [ ] **Passo 6.1:** Posicione o cursor sobre uma janela ou ícone.
- [ ] **Passo 6.2:** Feche a pinça indicador e **mantenha fechada** por mais de 400 ms.
  - *Resultado esperado:* O sistema entra em modo de arraste (`drag_start`).
- [ ] **Passo 6.3:** Mova a mão enquanto segura a pinça.
  - *Resultado esperado:* O ícone ou janela é arrastado junto com o cursor.
- [ ] **Passo 6.4:** Abra a pinça (separe os dedos).
  - *Resultado esperado:* O botão do mouse é liberado (`drag_end`) e o objeto é solto na posição atual.

---

### 7. Perda de Rastreamento durante Arraste (Teste Crítico de Segurança)
- [ ] **Passo 7.1:** Inicie um arraste com a pinça sustentada.
- [ ] **Passo 7.2:** Com o arraste ativo, retire repentinamente a mão do campo da câmera (ou cubra a lente).
  - *Resultado esperado:* O cursor congela imediatamente e o botão do mouse é solto (`release_all`). Nenhuma seleção ou botão fica preso no Windows.

---

### 8. Rolagem Vertical (Dois Dedos)
- [ ] **Passo 8.1:** Abra uma página web longa ou documento com barra de rolagem.
- [ ] **Passo 8.2:** Faça o gesto de **Dois Dedos** (indicador e médio estendidos para cima, polegar e anelar dobrados).
- [ ] **Passo 8.3:** Mova a mão para baixo.
  - *Resultado esperado:* A página rola para baixo suavemente.
- [ ] **Passo 8.4:** Mova a mão para cima.
  - *Resultado esperado:* A página rola para cima.

---

### 9. Parada de Emergência Global (Hotkey `Ctrl+Shift+F12`)
- [ ] **Passo 9.1:** Com os comandos reais ativos, abra outro programa (ex: Bloco de Notas ou Navegador) de forma que o HandControl fique em segundo plano.
- [ ] **Passo 9.2:** Pressione o atalho global **`Ctrl + Shift + F12`**.
  - *Resultado esperado:* O app entra em estado de emergência (`EMERGÊNCIA`), botões são liberados e o cursor deixa de ser controlado.
- [ ] **Passo 9.3:** Retorne à janela do HandControl. O status exibirá **"🔴 EMERGÊNCIA"** e a borda do vídeo estará vermelha.
- [ ] **Passo 9.4:** Clique no botão verde **"✅ Retomar (limpar emergência)"** para restabelecer a operação normal.

---

### 10. Botão de Emergência da Interface
- [ ] **Passo 10.1:** Durante a operação normal, clique diretamente no botão vermelho **"🛑 Parada de Emergência"**.
  - *Resultado esperado:* Bloqueio imediato de todas as ações e borda do vídeo vermelha.

---

### 11. Atalhos de Teclado e Teclas de Mídia (Fase 5)
- [ ] **Passo 11.1 (Atalhos Permitidos):** Teste os atalhos da allowlist (`Alt+Tab`, `Ctrl+C`, `Ctrl+V`, `Ctrl+Z`, `Win+D`, `Esc`).
  - *Resultado esperado:* Cada atalho executa a ação correspondente no Windows.
- [ ] **Passo 11.2 (Teclas de Mídia):** Teste os comandos multimídia (`media_play_pause`, `media_next`, `media_prev`, `volume_up`, `volume_down`, `volume_mute`).
  - *Resultado esperado:* O volume do Windows altera ou o player em foco responde. O histórico da interface exibe o aviso explicativo: `(requer suporte do app em foco)`.
- [ ] **Passo 11.3 (Bloqueio de Atalhos Perigosos):** Tente submeter atalhos proibidos (ex: `Ctrl+Alt+Del`, `Win+L`, `Alt+F4`).
  - *Resultado esperado:* O validador rejeita imediatamente a ação como `REFUSED: Dangerous shortcut is blocked by security policy`.

---

### 12. Liberação de Teclas e Prevenção de Teclas Presas (Fase 5)
- [ ] **Passo 12.1:** Execute uma ação de atalho com modificador (ex: `Alt+Tab` ou `Ctrl+C`).
- [ ] **Passo 12.2:** Verifique se as teclas modificadoras (`Ctrl`, `Alt`, `Shift`, `Win`) foram liberadas no Windows e não ficaram presas pressionadas.
- [ ] **Passo 12.3:** Retire a mão da câmera ou pause o aplicativo no meio de uma ação.
  - *Resultado esperado:* `release_all()` é executado e todas as teclas são liberadas imediatamente.

---

### 13. Gerenciamento de Janelas e Multi-monitor (Fase 6)
- [ ] **Passo 13.1 (Enumeração de Janelas):** No painel "Gerenciamento de Janelas", clique no botão **"🔄 Atualizar"**.
  - *Resultado esperado:* O combobox lista todas as janelas abertas elegíveis (título visível, tamanho > 10x10), filtrando componentes internos do sistema (Progman, Shell_TrayWnd, etc.).
- [ ] **Passo 13.2 (Arraste Relativo Sem Salto Inicial):**
  - Selecione uma janela (ex: Bloco de Notas) ou deixe no modo automático (janela ativa).
  - Feche o punho esquerdo (**Fist**) para agarrar a janela.
  - *Resultado esperado:* A janela é agarrada instantaneamente. **Não há salto ou teletransporte** da janela ao fechar a mão (zero initial jump).
  - Desloque a mão em qualquer direção: a janela segue proporcionalmente o movimento, limitada a 60 Hz.
  - Abra a mão esquerda: a janela é solta e permanece na posição alcançada.
- [ ] **Passo 13.3 (Perda de Rastreamento Durante Arraste):**
  - Com a janela sendo arrastada pelo punho, retire a mão da câmera ou cubra a lente.
  - *Resultado esperado:* O arraste é cancelado com segurança (`release_all`), mantendo a janela estável no último local válido.
- [ ] **Passo 13.4 (Minimizar, Maximizar e Alternar):**
  - Teste os botões rápidos no painel de janelas: **"Minimizar"**, **"Maximizar"** e **"Alternar"**.
  - *Resultado esperado:* A janela selecionada responde imediatamente alternando entre os estados normais, minimizados e maximizados.
- [ ] **Passo 13.5 (Transferência Multi-monitor):**
  - Em um computador com 2 ou mais telas conectadas, selecione uma janela e clique em **"🖥️ Próximo Monitor"**.
  - *Resultado esperado:* A janela é movida para a próxima tela em sequência circular, centralizada na área de trabalho e ajustada para caber no monitor de destino. Se estiver maximizada, é restaurada automaticamente antes da transferência.
- [ ] **Passo 13.6 (Proteção contra Janelas Elevadas / Administrador):**
  - Abra uma janela como Administrador (ex: PowerShell ou Gerenciador de Tarefas).
  - Tente agarrá-la ou movê-la via HandControl rodando como usuário comum.
  - *Resultado esperado:* A ação é recusada com aviso descritivo no histórico de ações (`Janela pertence a um processo elevado (Administrador)...`), prevenindo bloqueios silenciosos de UIPI.

---

### 14. Editor de Perfis, Gestos Dinâmicos e Bandeja do Sistema (Fase 7)

- [ ] **Passo 14.1 (Gerenciamento e Criação de Perfis):**
  - Na barra superior da janela principal, clique em **"⚙️ Editar Perfis"**.
  - No diálogo do Editor de Perfis, clique em **"➕ Novo Perfil"**, insira o nome `Apresentacao` e confirme.
  - Clique em **"📋 Duplicar"** para clonar o perfil e em **"🗑️ Excluir"** para remover o clone.
  - Verifique que o perfil `Apresentacao` agora aparece no combobox de perfis ativos na barra superior da janela principal.

- [ ] **Passo 14.2 (Exportação e Importação Segura de JSON):**
  - Selecione o perfil `Apresentacao` e clique em **"📤 Exportar Perfil"**. Salve o arquivo `apresentacao.json`.
  - Abra o arquivo exportado em um editor de texto e verifique a formatação JSON legível e estruturada.
  - No HandControl, clique em **"📥 Importar Perfil"** e selecione o arquivo salvo.
  - Verifique que o perfil é importado com sucesso (renomeado automaticamente se houver conflito de nome).

- [ ] **Passo 14.3 (Rejeição Graciosa de JSON Malformado ou Inválido):**
  - Crie um arquivo de texto com JSON truncado ou faltando chaves obrigatórias.
  - Clique em **"📥 Importar Perfil"** e tente carregar este arquivo inválido.
  - *Resultado esperado:* O aplicativo exibe uma mensagem de erro clara (**"JSON inválido ou corrompido"** / **"Estrutura incompatível com ProfileModel"**), sem travar nem corromper a configuração existente.

- [ ] **Passo 14.4 (Gestos Dinâmicos: Deslize e Círculo):**
  - No Editor de Perfis, clique em **"➕ Adicionar Regra"**.
  - Configure o gatilho como `swipe_left`, ação `key_press`, parâmetro `left` (para retroceder slide).
  - Adicione outra regra com gatilho `swipe_right`, ação `key_press`, parâmetro `right` (para avançar slide).
  - Adicione uma regra com gatilho `circle_cw`, ação `system_command`, parâmetro `volume_up`.
  - Clique em **"Salvar e Fechar"** e inicie o rastreamento (Modo Real ou Simulação).
  - Realize um movimento rápido e retilíneo com a mão para a esquerda / direita e círculos no sentido horário.
  - *Resultado esperado:* O histórico de ações registra a detecção do deslize/círculo correspondente e dispara o comando configurado sem exigir qualquer alteração no código Python.

- [ ] **Passo 14.5 (Gravação e Reconhecimento de Trajetória Personalizada via DTW):**
  - No Editor de Perfis, ao editar ou adicionar uma regra, selecione o gatilho `custom_trajectory`.
  - Clique em **"🔴 Gravar Trajetória"** e desenhe um padrão no ar (ex: uma letra "V" ou "Z") diante da câmera dentro do tempo limite.
  - Verifique que a trajetória capturada é reamostrada e exibida no visualizador do editor.
  - Salve a regra e teste reproduzir o gesto diante da câmera.
  - *Resultado esperado:* O algoritmo DTW (Dynamic Time Warping) reconhece a trajetória personalizada com invariância a variações moderadas de velocidade e escala.

- [ ] **Passo 14.6 (Detecção e Alerta de Conflitos de Regras):**
  - No Editor de Perfis, configure duas regras com o mesmo gesto (ex: `fist`) apontando para ações distintas com a mesma prioridade.
  - *Resultado esperado:* O banner de validação no topo do editor exibe um aviso em vermelho (**"⚠️ 1 conflito direto detectado: [fist] mapeado para múltiplas ações com a mesma prioridade"**).
  - Ajuste a prioridade de uma das regras ou altere o gesto: o banner retorna para o estado verde (**"✅ Nenhuma colisão ou conflito detectado neste perfil."**).

- [ ] **Passo 14.7 (Minimização para a Bandeja do Sistema / System Tray):**
  - Clique no botão de minimizar da janela principal (`_`) ou feche a janela (`X`).
  - *Resultado esperado:* A janela é ocultada da barra de tarefas e o ícone do HandControl permanece visível na área de notificação do Windows (System Tray).
  - Dê um duplo clique no ícone da bandeja (ou clique com o botão direito e selecione **"Abrir HandControl"**).
  - *Resultado esperado:* A janela principal é restaurada imediatamente no centro da tela.

- [ ] **Passo 14.8 (Indicador Dinâmico de Privacidade no Tray):**
  - Com a câmera parada, o ícone da bandeja exibe badge cinza (**X**) e tooltip indicando *"HandControl — Parado"*.
  - Ao iniciar em modo de simulação, o ícone exibe badge verde (**S**) e tooltip *"HandControl — Simulação Ativa"*.
  - Ao ativar o modo real, o ícone exibe badge âmbar/laranja (**R**) e tooltip *"HandControl — MODO REAL ATIVO (Câmera monitorando)"*.
  - Ao pausar, o ícone exibe badge amarelo (**P**) e tooltip *"HandControl — Pausado"*.

- [ ] **Passo 14.9 (Persistência Atômica e Recuperação de Configuração Corrompida):**
  - Altere opções no editor de perfis e feche o aplicativo.
  - Verifique a criação de `%APPDATA%\HandControl\config.json` e `%APPDATA%\HandControl\config.bak`.
  - Simule corrupção apagando o conteúdo de `config.json` deixando-o com bytes inválidos.
  - Abra o aplicativo novamente.
  - *Resultado esperado:* O aplicativo detecta o arquivo corrompido, recupera automaticamente a partir do backup `.bak` (ou restaura a configuração padrão segura se ambos falharem) e continua a inicialização sem travar.



