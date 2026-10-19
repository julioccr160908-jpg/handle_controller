# HandControl — Especificação Mestra (v2)

Documento de referência para desenvolvimento com agente de código. Salve como `HANDCONTROL_SPEC.md` na raiz do projeto. Cada sessão implementa **uma única fase** (seção 12). O prompt de abertura de sessão está na seção 15.

## 1. Papel e modo de trabalho

Você é engenheiro de software sênior em Python, visão computacional, automação do Windows e aplicações desktop em tempo real. Está construindo o **HandControl**: um app desktop que controla o Windows com gestos das duas mãos capturados pela webcam.

Protocolo de toda sessão:

1. Leia este documento e inspecione o projeto (estrutura, dependências, testes, git). Não presuma que o projeto está vazio; preserve o que já funciona.
2. Implemente **somente a fase indicada**. Não adiante fases futuras; no máximo deixe interfaces preparadas.
3. Se houver ambiguidade que mude a arquitetura, siga o padrão desta especificação e registre a decisão em `docs/DECISIONS.md`. Só pergunte ao usuário se for bloqueante.
4. Faça alterações pequenas e incrementais. Não reescreva arquivos que funcionam sem motivo.
5. Ao final, rode testes e lint e entregue o relatório no formato da seção 14.

## 2. Regras invioláveis

Estas regras valem em todas as fases e têm prioridade sobre qualquer outra instrução.

1. **O reconhecimento nunca executa comandos do sistema.** O fluxo é sempre: câmera → rastreamento → normalização → gestos → eventos → mapeamento → controlador de ações → executores.
2. **Modo de simulação por padrão.** Comandos reais só após ativação explícita do usuário, a cada inicialização.
3. **Perda de rastreamento nunca autoriza continuar uma ação.** Ao perder a mão, pausar, trocar de perfil ou encerrar: soltar botões do mouse, soltar teclas, cancelar arraste de janela.
4. **Nenhum arquivo de configuração executa código.** Proibido `eval`, `exec`, `shell=True`, `pickle` e importação dinâmica a partir de JSON. Ações vêm de uma lista de permissões (allowlist) fixa no código.
5. **A parada de emergência não depende da câmera nem de gestos** (hotkey global + botão na interface).
6. **A interface nunca bloqueia.** Widgets só são atualizados na thread da GUI.
7. **Sem filas ilimitadas.** Se o processamento atrasar, descarte quadros antigos e processe o mais recente.
8. **Nenhum vídeo ou imagem é gravado em disco.** Gestos gravados guardam apenas trajetórias normalizadas.
9. **Funciona 100% offline** durante o uso normal. Sem telemetria.
10. **Cada dependência nova exige justificativa** registrada em `docs/DECISIONS.md`.

## 3. Decisões técnicas fixas

- **Sistema alvo:** Windows 10 e 11, 64 bits.
- **Python:** 3.11 (confirme a compatibilidade do MediaPipe com a versão antes de fixar; se houver conflito, use a versão mais nova suportada por todas as dependências).
- **Dependências:** OpenCV (captura), MediaPipe Hand Landmarker (rastreamento), PySide6 (interface), Pydantic v2 (configuração), PyAutoGUI (mouse e teclado, atrás de uma camada de abstração), PyWinCtl mais `ctypes`/Win32 (janelas e monitores), Pytest, Ruff. Fixe versões compatíveis entre si em `pyproject.toml` ou `requirements.txt` somente depois de verificar a disponibilidade.
- **PyAutoGUI:** desative o failsafe e zere `PAUSE` (`pyautogui.FAILSAFE = False`, `pyautogui.PAUSE = 0`) **somente dentro da camada de abstração**, e compense com a parada de emergência própria.
- **Hotkey de emergência global:** `RegisterHotKey` da API do Windows via `ctypes`, sem biblioteca extra. Padrão: `Ctrl+Alt+F12`, configurável.
- **Mídia:** teclas virtuais `VK_MEDIA_*` e `VK_VOLUME_*` via `SendInput` (`ctypes`).
- **DPI:** o processo deve declarar *Per-Monitor DPI Awareness v2* na inicialização, antes de criar qualquer janela, para que as coordenadas de monitores e janelas sejam consistentes.
- **Persistência:** JSON validado por Pydantic, em `%APPDATA%\HandControl\`.
- **Logging:** biblioteca padrão, com rotação de arquivo e sem dados sensíveis.
- **Hardware de referência:** na Fase 1, registre em `docs/BENCHMARKS.md` o modelo de CPU, RAM, webcam e versão do Windows em que as metas serão medidas.

## 4. Arquitetura

### 4.1 Estrutura de pastas

```
handcontrol/
  app.py                 # ponto de entrada, composição dos módulos
  capture/               # webcam, thread de captura, quadro mais recente
  tracking/              # MediaPipe, dados das mãos, estado de rastreamento
  features/              # normalização, dedos, distâncias, trajetórias
  gestures/              # regras, máquina de estados, trajetórias gravadas
  mapping/               # perfis, resolução de conflitos, cooldowns
  actions/               # controlador central + executores
    mouse.py  keyboard.py  media.py  windows.py  base.py
  config/                # modelos Pydantic, carga e salvamento, migração
  ui/                    # PySide6: painel, editor, configurações, histórico, bandeja
  safety/                # modo de simulação, emergência, liberação de entradas
  metrics/               # FPS, latência, memória
tests/
docs/  (DECISIONS.md, BENCHMARKS.md)
```

### 4.2 Contratos de dados (definir na Fase 1 e manter estáveis)

- `HandFrame`: timestamp monotônico, id do quadro, lista de `HandObservation` (lateralidade, confiança da lateralidade, 21 landmarks normalizados, landmarks em coordenadas de imagem, confiança de presença).
- `HandFeatures`: dedos estendidos/dobrados, distâncias normalizadas (por exemplo, pela largura da palma), orientação da palma, velocidade, validade dos dados.
- `GestureEvent`: id do gesto, fase (`STARTED`, `HELD`, `ENDED`, `CANCELLED`), mão, timestamp, confiança, payload (deslocamento, trajetória).
- `ActionRequest` e `ActionResult`: ação da allowlist, parâmetros validados, resultado (`EXECUTED`, `SIMULATED`, `REFUSED`, `FAILED`) e motivo.

### 4.3 Pipeline e concorrência

- **Thread de captura:** lê a webcam e mantém só o quadro mais recente (buffer de tamanho 1). Nunca acumula.
- **Thread de rastreamento:** pega o quadro mais recente, roda o MediaPipe, normaliza, reconhece gestos e publica eventos.
- **Thread da GUI:** recebe quadros e eventos por sinais Qt (a imagem para exibição deve ser reduzida e limitada a no máximo 30 atualizações por segundo).
- **Thread de ações:** executa ações, de modo que o arraste de janelas e comandos nunca bloqueiem rastreamento ou GUI.
- **Encerramento coordenado:** parar threads, liberar câmera e modelo, soltar entradas pressionadas.

### 4.4 Rastreamento

- Até duas mãos. Não presuma identidade estável entre quadros: trate troca de lateralidade, oclusão e reaparecimento.
- Uma mão é considerada perdida após N quadros sem detecção (configurável; padrão 5). A recuperação só reativa ações após o gesto ser novamente confirmado.
- Considere o efeito de espelhamento da webcam na lateralidade e documente a escolha.

### 4.5 Normalização

- Medidas sempre relativas a uma escala da própria mão (por exemplo, distância pulso–base do dedo médio ou largura da palma). Nunca use coordenadas absolutas como único critério.
- Calcular: dedos estendidos, distâncias entre pontas, orientação da palma, deslocamento entre quadros, trajetórias normalizadas, distância entre as duas mãos e validade dos dados.

### 4.6 Motor de gestos

- Independente de UI, de mapeamento e do Windows.
- Máquina de estados por gesto: `IDLE → CANDIDATE → ACTIVE → RELEASING → IDLE`, com tempo mínimo de confirmação, tempo de saída, histerese (limiar de entrada diferente do limiar de saída) e cooldown.
- **Gestos de clique geram um único evento por ativação.** Um novo clique exige sair do gesto.
- Configurável por gesto: tolerâncias, tempos, confiança mínima, mão utilizada e se exige as duas mãos.
- Gestos dinâmicos (v1): movimento horizontal, vertical, aproximação/afastamento das mãos e sequências gravadas pelo usuário (comparação de trajetórias normalizadas, por exemplo DTW simples). Gesto circular só se couber sem complexidade extra.
- **Sem machine learning na v1.**

### 4.7 Mapeamento

- Consulta o perfil ativo, resolve conflitos por prioridade, valida parâmetros, aplica cooldown e limite de repetição, verifica se a ação é permitida no estado atual e registra o resultado.
- Trocar a ação de um gesto não pode exigir alterar o motor de gestos.

## 5. Gestos padrão (perfil inicial, todos configuráveis)

Todo gesto padrão precisa ser **distinguível dos demais por regra geométrica**, e o validador de conflitos (seção 4.7) deve alertar quando duas regras puderem ser confundidas.

| Gesto | Mão | Regra | Efeito padrão |
| --- | --- | --- | --- |
| Apontar | Preferida (direita) | Indicador estendido, demais dobrados | Move o cursor (a mão só controla o cursor nesta pose: é a **embreagem**) |
| Pinça indicador | Preferida | Ponta do polegar perto da ponta do indicador | Clique esquerdo (1 evento por ativação) |
| Pinça médio | Preferida | Ponta do polegar perto da ponta do médio | Clique direito |
| Pinça sustentada | Preferida | Pinça indicador mantida por mais de 400 ms | Arrastar (solta ao abrir a pinça) |
| Dois dedos | Preferida | Indicador e médio estendidos, demais dobrados | Rolagem pelo movimento vertical |
| Punho | Esquerda | Todos os dedos dobrados | Agarra a janela selecionada e a move; solta ao abrir a mão |
| Duas palmas abertas | Ambas | Ambas abertas, mantidas por 1 s | Alterna pausa dos comandos |

Observações:

- O clique por pinça deve ser distinguido do arraste pelo tempo de permanência, e o cursor deve ficar congelado durante a pinça para que o clique não se desloque.
- Defina limiares de pinça relativos à escala da mão, com histerese.
- Os gestos de teclado e mídia não vêm no perfil padrão; o usuário os cria no editor (Fase 7), usando as ações da seção 7.

## 6. Controle do mouse

- **Mapeamento absoluto com região ativa:** um retângulo configurável do quadro da câmera (padrão: região central, com margem) é mapeado para a área total das telas. A mão não precisa chegar à borda da imagem para alcançar a borda da tela.
- Calibração da região ativa pela interface.
- Suavização configurável (por exemplo, filtro One Euro ou média exponencial adaptativa), zona morta contra tremor, sensibilidade e velocidade máxima.
- Coordenadas em espaço virtual do Windows (inclui valores negativos em multi-monitor).
- Ao perder a mão: congelar o cursor, soltar botões. Ao reaparecer: não permitir salto brusco; exigir a pose de apontar novamente.
- Indicador visual do modo (por exemplo, cor da borda do vídeo e ícone na bandeja).

## 7. Teclado e mídia

**Allowlist da v1** (qualquer outra ação é recusada):

- Atalhos: `Alt+Tab`, `Ctrl+C`, `Ctrl+V`, `Ctrl+Z`, `Win+D`, `Esc` e outras combinações adicionadas explicitamente pelo usuário a partir de uma lista validada de teclas.
- Mídia: reproduzir/pausar, próxima, anterior, volume + e −, silenciar.

Regras:

- Validar o atalho antes de executar; limitar repetição por segundo.
- Ao perder o rastreamento, pausar ou encerrar, soltar todas as teclas.
- Se uma ação de mídia depender de suporte do aplicativo em foco, informar na interface.
- Combinações perigosas do sistema (por exemplo `Ctrl+Alt+Del`) não entram na allowlist.

## 8. Gerenciamento de janelas (funcionalidade central)

### 8.1 Seleção

- Janela ativa (padrão) e seleção por lista de janelas elegíveis.
- Seleção vinculada ao cursor (janela sob o ponteiro) quando viável.
- Filtrar janelas ocultas, fechadas, sem título ou não manipuláveis. Arquitetura preparada para seleção visual mais avançada no futuro.

### 8.2 Arrastar

Fluxo: selecionar → reconhecer o gesto de agarrar → registrar posição inicial da janela e da mão → aplicar deslocamento relativo → soltar ao fim do gesto.

- Preservar o ponto de agarre (sem salto inicial).
- Atualizar a posição a no máximo 60 Hz, com coalescência de atualizações (se houver atraso, usar só a última posição).
- Perda de rastreamento ou pausa cancela o arraste e deixa a janela onde está.

### 8.3 Minimizar, maximizar, restaurar

- Ações independentes, com gestos configuráveis.
- Consultar o estado atual (normal, minimizada, maximizada) antes de agir.

### 8.4 Múltiplos monitores

- Enumerar monitores e geometrias em espaço virtual (coordenadas negativas, resoluções e escalas diferentes, monitores acima/abaixo/ao lado).
- A travessia entre telas segue a disposição configurada no Windows; nunca assumir que o segundo monitor está à direita.
- Reenumerar quando a configuração mudar (mensagem `WM_DISPLAYCHANGE` ou reconsulta periódica leve).
- Tratar o comportamento do Windows ao maximizar em outro monitor e ao restaurar depois de transferir.

### 8.5 Limites

- Janelas de processos elevados (administrador) não podem ser controladas por um processo sem privilégio: **avisar o usuário na interface e no histórico**, nunca falhar em silêncio.
- Tratar janelas que desaparecem, operações recusadas e mudanças de monitor durante o arraste.

## 9. Interface (PySide6)

- **Painel principal:** vídeo com landmarks, mãos identificadas, estado do rastreamento, gesto reconhecido, última ação, iniciar/pausar/parar, FPS e latência, estado do modo de segurança (simulação ou real) e botão de emergência sempre visível.
- **Editor de gestos:** lista, criar/editar/duplicar/desativar/excluir, regra geométrica ou gravação de trajetória, ajuste de tolerâncias e tempos, pré-visualização ao vivo, teste em simulação, associação de ações e indicação de conflitos.
- **Configurações:** câmera, resolução, taxa de atualização, mão preferida, suavização, sensibilidade, região ativa, monitores, perfis, inicialização com o Windows (opt-in).
- **Histórico de eventos:** gesto reconhecido, ação solicitada, concluída ou recusada, perda de rastreamento, erros. Sem imagens.

## 10. Segundo plano e bandeja

Diferenciar claramente quatro estados: **minimizar** (continua rastreando se o controle estiver ativo), **pausar** (para de interpretar gestos), **desligar a câmera** (libera o dispositivo) e **encerrar** (finaliza o processo).

- Menu da bandeja: abrir janela, ativar/desativar controle, pausar imediatamente, encerrar.
- **Indicador de câmera ativa sempre visível** (ícone da bandeja muda de estado), por privacidade.
- Sem captura de imagens quando o rastreamento estiver desativado.
- Sem inicialização automática com o Windows, a menos que o usuário ative nas configurações.

## 11. Segurança, desempenho e persistência

### 11.1 Segurança

- Modo de simulação por padrão; ativação explícita dos comandos reais; botão e hotkey de emergência; pausa independente de gestos; limites de repetição; validação de configurações; registro de erros.
- Ao ativar comandos reais pela primeira vez na sessão, exibir confirmação clara.

### 11.2 Metas de desempenho (valores iniciais; ajuste após medir no hardware de referência)

| Métrica | Meta |
| --- | --- |
| FPS de processamento a 640×480 | ≥ 20 (alvo 30) |
| Latência do pipeline (quadro capturado → evento de gesto) | < 100 ms |
| Latência do evento até a ação executada | < 50 ms |
| Crescimento de memória em 30 min de uso | sem tendência de alta contínua |
| Fila de quadros pendentes | sempre ≤ 1 |

Medir FPS de captura e rastreamento, latência por etapa, tempo de execução das ações e memória (`tracemalloc` e `resource`/`ctypes`, sem bibliotecas pesadas). Exibir no painel e registrar em `docs/BENCHMARKS.md`.

### 11.3 Persistência

JSON em `%APPDATA%\HandControl\` com:

- `schema_version` (inteiro) e função de migração entre versões.
- Configurações globais (câmera, resolução, suavização, região ativa, hotkey, opções de inicialização).
- Perfis: `profile_id`, `name`, lista de gestos (id, nome, regra, tolerâncias, tempos, mão, ativo) e mapeamentos (gesto → ação da allowlist + parâmetros).
- Trajetórias gravadas: pontos normalizados, sem vídeo.
- Escrita atômica (arquivo temporário e substituição), backup do último arquivo válido e recuperação se o JSON estiver corrompido (carregar o backup ou os padrões, avisar o usuário).
- Todo arquivo carregado passa pela validação Pydantic; falhou, é recusado com mensagem clara.

## 12. Fases de implementação

Cada fase termina com testes passando e critérios de aceitação atendidos. Não avance sem o aval do usuário.

### Fase 1 — Captura, rastreamento e visualização

- Estrutura do projeto, `pyproject.toml`, Ruff, Pytest, logging, `docs/`.
- Captura em thread com buffer de tamanho 1; MediaPipe com até duas mãos; contratos de dados da seção 4.2; janela PySide6 com vídeo, landmarks, lateralidade, FPS e latência; iniciar/pausar/parar.
- **Nenhuma ação no sistema.**
- **Aceitação:** 20+ FPS em 640×480 no hardware de referência; encerrar libera a câmera; desconectar a webcam não derruba o app (mostra erro e permite reconectar); testes unitários dos contratos e do tratamento de perda de mão.

### Fase 2 — Normalização e motor de gestos

- Módulo de features; gestos estáticos (palma, punho, pinça, dedos selecionados); máquina de estados com histerese, tempos e cooldown; eventos `GestureEvent`.
- **Aceitação:** testes com landmarks sintéticos e gravados (fixtures JSON) cobrindo cada gesto, histerese e um evento por ativação; sem dependência de UI ou Windows; painel mostra o gesto reconhecido.

### Fase 3 — Mapeamento, controlador de ações e simulação

- Perfil padrão (seção 5), mapeamento, allowlist, controlador central, executores falsos (simulação), histórico de eventos, modo de segurança e botão de emergência.
- **Aceitação:** gestos geram `ActionResult` com status `SIMULATED`; ação fora da allowlist é recusada e registrada; testes de conflito e de cooldown.

### Fase 4 — Mouse real

- Hotkey global de emergência, camada de abstração do mouse, região ativa e calibração, suavização, zona morta, clique, clique direito, duplo, arraste e rolagem.
- **Aceitação:** teste manual guiado (lista em `docs/MANUAL_TESTS.md`): alcançar os quatro cantos da tela, clicar sem deslocar o cursor, soltar botão ao perder a mão e na emergência; testes automatizados com executor falso.

### Fase 5 — Teclado e mídia

- Executores de teclado e mídia, validação de atalhos, limite de repetição, liberação de teclas.
- **Aceitação:** cada ação da allowlist executa no Windows; nenhuma tecla fica presa ao perder a mão, pausar ou encerrar.

### Fase 6 — Janelas e multi-monitor

- Seleção, arraste com ponto de agarre, minimizar/maximizar/restaurar, enumeração de monitores em espaço virtual, travessia entre monitores, aviso para janelas elevadas.
- **Aceitação:** teste manual com dois monitores em disposições diferentes (incluindo monitor à esquerda e acima); sem salto no início do arraste; perda de mão cancela o arraste; testes unitários da geometria dos monitores com coordenadas negativas e escalas distintas.

### Fase 7 — Editor de gestos, perfis, gestos dinâmicos e bandeja

- Editor visual, gravação de trajetória, validação de conflitos, perfis (criar, duplicar, exportar, importar com validação), bandeja do sistema, configurações completas.
- **Aceitação:** criar um gesto novo pela interface e usá-lo sem editar código; importar um JSON malformado é recusado sem travar; o app continua rastreando minimizado na bandeja.

### Fase 8 — Polimento

- Teste de estabilidade de 30 minutos, revisão de vazamentos, métricas finais, empacotamento (por exemplo PyInstaller, se o usuário pedir) e documentação de uso.

## 13. Fora de escopo (não implementar)

Reconhecimento de fala, linguagem natural, IA generativa, treinamento de modelos de machine learning, servidor HTTP, frontend web, banco de dados, serviços externos, telemetria e qualquer recurso não descrito acima.

## 14. Formato do relatório ao final de cada sessão

1. O que foi implementado (por módulo).
2. Arquivos criados e alterados.
3. Resultado de testes e lint (comandos executados).
4. Como validar manualmente (passo a passo curto).
5. Decisões tomadas e registradas em `docs/DECISIONS.md`.
6. Limitações conhecidas e riscos.
7. Próxima fase sugerida.

Se algo não foi testado de fato (por exemplo, por falta de webcam ou segundo monitor), diga isso explicitamente em vez de afirmar que funciona.

## 15. Prompt de abertura de sessão (copie e cole)

```
Leia HANDCONTROL_SPEC.md por completo e inspecione o projeto atual.
Implemente SOMENTE a Fase [N] da seção 12.
Respeite as regras invioláveis (seção 2) e os critérios de aceitação da fase.
Não adiante fases futuras. Ao final, rode testes e lint e entregue o relatório
no formato da seção 14, incluindo como eu valido manualmente.
```
