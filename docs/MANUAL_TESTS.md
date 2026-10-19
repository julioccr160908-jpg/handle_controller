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
