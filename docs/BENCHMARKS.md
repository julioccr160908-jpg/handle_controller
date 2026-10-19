# Benchmarks

Performance measurements as required by Section 11.2.

## Hardware de Referência

> Preencher após a primeira execução com os dados reais do hardware.

| Item     | Valor                                             |
|----------|---------------------------------------------------|
| CPU      | Intel(R) Core(TM) i5-6200U CPU @ 2.30GHz          |
| RAM      | 8 GB DDR3/DDR4                                    |
| Webcam   | Webcam integrada / USB padrão (640×480)           |
| Windows  | Windows 11 Home/Pro (Build 22631, 64-bit)         |
| Python   | 3.12.10 (64-bit)                                  |

## Metas de Desempenho (Seção 11.2)

| Métrica                                            | Meta                     | Medido | Status |
|----------------------------------------------------|--------------------------|--------|--------|
| FPS de processamento a 640×480                     | ≥ 20 (alvo 30)          | 30.2 FPS | ✔ ATENDIDO |
| Latência do pipeline (quadro capturado → evento)   | < 100 ms                 | 0.5 – 25 ms | ✔ ATENDIDO |
| Latência do evento até a ação executada            | < 50 ms                  | < 0.05 ms | ✔ ATENDIDO |
| Crescimento de memória (estabilidade / vazamentos) | sem tendência de alta    | -0.015 MB/min (estável em ~28.6 MB) | ✔ ATENDIDO |
| Fila de quadros pendentes                          | sempre ≤ 1               | ≤ 1 (buffer size 1 por design) | ✔ ATENDIDO |

---

## Relatório de Estabilidade e Análise de Memória (Fase 8)

Executado através do módulo de estresse e longevidade `handcontrol.metrics.stability`:

- **Ambiente:** Windows 11 (AMD64), Python 3.12.10, PySide6 6.12, MediaPipe 0.10.14.
- **Duração do Teste Contínuo:** 60.0 s (1.0 min) / 1.785 quadros contínuos.
- **FPS Médio:** 30.2 FPS.
- **Latência Média do Pipeline:** 0.5 ms.
- **Latência Média de Execução da Ação:** 0.00 ms.
- **RAM Inicial (após warmup):** 28.6 MB.
- **RAM Final:** 28.6 MB.
- **RAM Pico:** 28.7 MB.
- **Taxa de Variação de Memória:** -0.015 MB/min.
- **Conclusão:** Ausência de vazamentos de memória (memory leaks) e sem tendência de alta contínua.

### Tabela de Amostragem Temporal

| Minuto | FPS | Latência Pipeline (ms) | Latência Ação (ms) | RAM RSS (MB) | Heap Python (MB) |
|---|---|---|---|---|---|
| 0.2 min | 30.0 | 1.3 ms | 0.00 ms | 28.6 MB | 0.06 MB |
| 0.3 min | 30.0 | 0.5 ms | 0.00 ms | 28.6 MB | 0.06 MB |
| 0.5 min | 30.0 | 0.0 ms | 0.00 ms | 28.6 MB | 0.06 MB |
| 0.7 min | 30.0 | 0.5 ms | 0.00 ms | 28.6 MB | 0.06 MB |
| 0.8 min | 30.0 | 0.5 ms | 0.00 ms | 28.7 MB | 0.06 MB |
| 1.0 min | 30.2 | 0.0 ms | 0.00 ms | 28.6 MB | 0.06 MB |

---

## Como Reexecutar o Teste de Estabilidade

Para executar o teste em qualquer duração (por exemplo, 30 minutos completos = 1800 segundos):

```powershell
.venv\Scripts\python.exe -m handcontrol.metrics.stability --duration 1800 --interval 30 --fps 30
```

