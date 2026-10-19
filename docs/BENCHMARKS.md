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

| Métrica                                            | Meta                     | Medido |
|----------------------------------------------------|--------------------------|--------|
| FPS de processamento a 640×480                     | ≥ 20 (alvo 30)          | —      |
| Latência do pipeline (quadro capturado → evento)   | < 100 ms                 | —      |
| Latência do evento até a ação executada            | < 50 ms                  | —      |
| Crescimento de memória em 30 min                   | sem tendência de alta     | —      |
| Fila de quadros pendentes                          | sempre ≤ 1               | ✔ (buffer size 1 por design) |

> As medições serão preenchidas após a primeira execução no hardware de referência.
> Os valores na coluna "Meta" são alvos iniciais sujeitos a ajuste.
