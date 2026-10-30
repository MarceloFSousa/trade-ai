# AI Trade

Sistema que pega os últimos candles do MetaTrader 5, junta com um prompt escrito em arquivo e pergunta para uma IA
(Google Gemini gratuito por padrão, ou Claude) qual a direção mais provável do **próximo candle**. Modos:

- **analyze** — análise ao vivo dos últimos candles fechados.
- **backtest** — percorre o histórico, pergunta para a IA candle a candle e mede o resultado.
- **web** — front-end no navegador com duas telas: Backtests e Automação (robô ao vivo, controlado por WebSocket).

Os dois modos geram um gráfico HTML (Plotly) que abre no navegador.

---

## 1. Pré-requisitos

| O quê | Observação |
|---|---|
| Windows | A biblioteca `MetaTrader5` só funciona no Windows. |
| Python 3.10+ | Testado com 3.13. Verifique com `python --version`. |
| MetaTrader 5 instalado | **Aberto e logado** na corretora enquanto o sistema roda. |
| Chave de API de uma IA | Gemini (grátis) ou Claude (pago). Veja a seção 3. |

O ativo que você for usar (ex.: `WIN$N`) precisa existir no MT5 da sua corretora.

---

## 2. Instalação (uma vez só)

Abra o terminal (PowerShell) na pasta do projeto:

```powershell
cd C:\projetos\ai-trade
```

Crie o ambiente virtual (isola as bibliotecas deste projeto):

```powershell
python -m venv .venv
```

Ative o ambiente. O prompt passa a mostrar `(.venv)` no começo:

```powershell
.venv\Scripts\activate
```

> Se o PowerShell bloquear o script de ativação, rode uma vez
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e tente de novo.
> Alternativa sem ativar: troque `python` por `.venv\Scripts\python.exe` em todos os comandos abaixo.

Instale as dependências:

```powershell
pip install -r requirements.txt
```

---

## 3. Configurar a chave da IA

Copie o modelo do arquivo de ambiente:

```powershell
copy .env.example .env
```

### Gemini — gratuito (padrão)

1. Acesse https://aistudio.google.com/apikey e entre com sua conta Google.
2. Clique em **Create API key** (crie ou escolha um projeto quando pedir) e copie a chave (começa com `AIza`).
3. Cole no `.env`:

```
GEMINI_API_KEY=AIza...
```

No `config.yaml` deixe `ai.provider: "gemini"` e `ai.model: "gemini-3.5-flash-lite"` (padrão).

Sobre o plano gratuito:

- Tem limite de chamadas **por minuto** e **por dia** (os números atuais ficam em https://aistudio.google.com/rate-limit).
  O sistema espaça as chamadas (`ai.min_interval_seconds`, padrão 6 s) e repete automaticamente quando o Google pede para esperar.
- Se a **cota do dia** acabar, o backtest para e mostra o resultado parcial com o aviso. A cota renova à meia-noite do horário do Pacífico (~4h/5h de Brasília).
- No plano gratuito o Google pode usar o conteúdo enviado para melhorar os produtos dele (aqui são só candles e o prompt).
- Velocidade: no plano gratuito o `gemini-3.5-flash-lite` respondeu em ~2 s por chamada; o `gemini-3.8-flash`
  analisa melhor, mas levou de 45 s a 130 s por chamada (um backtest de 50 chamadas pode passar de 1 hora).
  Cada chamada tem limite de `ai.timeout_seconds` (padrão 120 s); se estourar, aquele candle conta como NEUTRAL.

### Claude — pago

1. Crie uma chave em https://console.anthropic.com → **Settings → API Keys** (precisa colocar créditos em **Billing**).
2. Cole no `.env`: `ANTHROPIC_API_KEY=sk-ant-...`
3. No `config.yaml`: `ai.provider: "claude"` e `ai.model: "claude-haiku-5-5"` (ou use `--provider claude --model claude-haiku-5-5`).

O `.env` está no `.gitignore` — nunca suba esse arquivo para o Git. O servidor web relê o `.env` a cada backtest,
então não precisa reiniciar depois de colar a chave.

---

## 4. Conferir se está tudo certo

Testes automáticos (não usam MT5 nem a API, não custam nada):

```powershell
python -m pytest
```

Teste de conexão com o MetaTrader 5 (deve imprimir 3 candles):

```powershell
python -c "from ai_trade.data.mt5_provider import MT5Provider; p = MT5Provider(); print(p.get_last_bars('WIN`$N', 'M5', 3)); p.close()"
```

> **Atenção ao `$` no PowerShell:** `$N` é lido como variável. Por isso os exemplos usam `'WIN$N'`
> entre aspas simples (ou `` WIN`$N `` dentro de aspas duplas). Sem isso o ativo vira só `WIN`.

Se der erro aqui, veja a seção [Problemas comuns](#9-problemas-comuns).

---

## Backtest pelo navegador (front-end)

O jeito mais simples de rodar o backtest. Suba o servidor:

```powershell
python main.py web
```

O navegador abre em http://127.0.0.1:8000 (use `--port 8080` para outra porta e `--no-open` para não abrir sozinho).
Deixe o terminal aberto; `Ctrl+C` para o servidor.

Na página:

1. Preencha **Ativo**, **Tempo gráfico**, **Início** e **Fim** (a data de fim entra inteira no período).
2. Em **Avançado (custo)**, ajuste quantos candles vão para a IA, analisar a cada K candles e o máximo de chamadas.
   Os valores iniciais vêm do `config.yaml`; a página lembra o último backtest que você rodou.
3. Clique em **Rodar backtest**. A barra mostra o progresso e a lista abaixo mostra cada previsão em tempo real.
4. **Cancelar** interrompe depois da chamada atual e mostra o resultado parcial.
5. No fim aparecem as métricas, o gráfico (candles, entradas ▲▼, saídas ✕ e curva de capital; passe o mouse
   para ver o raciocínio) e a tabela de operações. O CSV também é salvo em `output/`.

Os backtests rodam um por vez. Se você disparar outro enquanto um roda, ele entra na fila.

---

## Automação (robô)

Na mesma página, aba **Automação**. O robô aplica a regra do backtest ao vivo: a cada candle fechado zera a posição
anterior, pergunta à IA e abre a nova posição, que fica aberta por um candle. Ele roda aqui, em Python, e envia as
ordens ao terminal; não é um EA instalado dentro do MetaTrader. Por isso o **terminal e o `main.py web` precisam
ficar abertos**. Fechar só a página não para o robô.

| Controle | O que faz |
|---|---|
| **Ligar** | Começa a operar. A primeira análise é no próximo candle fechado. |
| **Pausar / Retomar** | Pausado não abre posições; a que estiver aberta sai no fim do candle. |
| **Zerar posição** | Fecha agora e pausa (sem pausar, entraria de novo no candle seguinte). |
| **Desligar** | Fecha a posição do robô e para. |
| **Aplicar parâmetros** | Vale a partir do próximo candle. Ativo, tempo gráfico e modo só mudam desligado. |

A página fala com o robô por WebSocket (`/ws/robot`): recebe o estado a cada segundo e envia os comandos pelo mesmo canal.

**Modo:** `Simulação` usa cotações reais e não envia nenhuma ordem. `MetaTrader 5` envia ordens à conta logada e pede confirmação ao ligar.

**Antes de usar o modo MetaTrader 5:**

- Opere o **contrato vigente** (`WINZ26`), e troque no vencimento. `WIN$N` é só histórico e não aceita ordens.
- Ligue o botão **Algo Trading** no terminal.
- O robô usa magic `770077` e só mexe nas posições dele. Se houver posição de outra origem no mesmo ativo, ele não entra.

**Proteções:**

- Conta **REAL** é recusada enquanto `robot.allow_real_account` for `false` no `config.yaml`.
- Toda ordem sai com **stop na corretora**, que continua valendo se o PC ou o robô cair.
- Ao atingir a perda máxima ou o limite de trades do dia, o robô pausa sozinho.
- Sinal que demora mais que o atraso máximo é descartado, para não entrar com preço velho.
- Se o robô cair com posição aberta, ao ligar de novo ele assume essa posição e a zera no fim do candle.

Os limites do plano gratuito do Gemini também valem aqui: em M5 são cerca de 100 chamadas por pregão.

---

## 5. Rodar a análise ao vivo

```powershell
python main.py analyze --symbol 'WIN$N' --timeframe M5 --bars 50
```

O que acontece:

1. Busca os últimos 50 candles **fechados** do WIN$N no M5.
2. Monta o prompt a partir de `prompts/trader.md`.
3. Envia para o Claude e recebe `UP`, `DOWN` ou `NEUTRAL`, com confiança (0–100%) e o raciocínio.
4. Mostra o resultado no terminal e abre o gráfico no navegador: candles enviados + seta da previsão.

Exemplo de saída:

```
WIN$N M5 | último candle: 2026-10-29 18:20:00
Previsão: UP (confiança 64%)
Raciocínio: ...
Gráfico: C:\projetos\ai-trade\output\analise_WIN_N_M5_20261029_182512.html
```

Cada análise é **uma** chamada à API.

---

## 6. Rodar o backtest

Comece pequeno para validar e ver o custo:

```powershell
python main.py backtest --symbol 'WIN$N' --timeframe M5 --start 2026-10-20 --end 2026-10-29 --step 5 --max-calls 5
```

Como funciona:

- A cada `--step` candles, o sistema envia para a IA os `--bars` candles **anteriores** (a IA nunca vê o futuro).
- `UP` = compra na **abertura** do candle seguinte; `DOWN` = venda na abertura.
- A saída é sempre no **fechamento** desse mesmo candle.
- `NEUTRAL` (ou erro da API) = não opera.
- Para quando atingir `--max-calls` chamadas.

Durante a execução aparece o progresso:

```
1840 candles de 2026-10-20 09:00 a 2026-10-29 18:25 | 5 chamadas à IA previstas
[1/5] 2026-10-20 13:10 -> DOWN (62%)
[2/5] 2026-10-20 13:35 -> UP (55%)
...
```

No fim, imprime as métricas, salva um CSV com todas as operações e abre o gráfico com:

- ▲ verde = entrada de compra, ▼ vermelho = entrada de venda;
- ✕ = saída (verde se ganhou, vermelho se perdeu);
- passando o mouse no marcador aparece o raciocínio da IA;
- curva de capital embaixo, tabela de métricas em cima e tabela de operações no rodapé.

### Quanto custa?

Cada chamada envia os candles + o prompt. Custo total ≈ `max-calls × custo por chamada`.
Rode primeiro com `--max-calls 5`. No Gemini gratuito não há cobrança, mas a cota diária é limitada;
no Claude veja o consumo em https://console.anthropic.com antes de aumentar.

| Parâmetro | Efeito no custo |
|---|---|
| `--max-calls` | Teto de chamadas. É a principal trava. |
| `--step` | Maior = menos candles analisados no período. |
| `--bars` | Mais candles por chamada = prompt maior = chamada mais cara. |

---

## 7. Parâmetros e `config.yaml`

Tudo tem padrão em `config.yaml`. O que você passar na linha de comando **sobrescreve** o arquivo.

| Linha de comando | config.yaml | Para quê |
|---|---|---|
| `--symbol` | `symbol` | Ativo, como aparece no MT5 (`WIN$N`, `WDO$N`, `PETR4`, `EURUSD`). |
| `--timeframe` | `timeframe` | `M1 M2 M3 M5 M10 M15 M30 H1 H4 D1 W1 MN1` |
| `--bars` | `bars` | Quantos candles a IA recebe. |
| `--model` | `ai.model` | Modelo (padrão `gemini-3.5-flash-lite`; também `gemini-3.8-flash`, `claude-haiku-5-5`). |
| `--provider` | `ai.provider` | Qual IA usar: `gemini` ou `claude` (registradas em `ai_trade/ai/factory.py`). |
| — | `ai.min_interval_seconds` | Espera mínima entre chamadas (para o limite por minuto do Gemini grátis). |
| `--prompt` | `prompt_file` | Arquivo de prompt alternativo. |
| `--start` / `--end` | `backtest.start` / `end` | Período do backtest (`AAAA-MM-DD`). |
| `--step` | `backtest.step` | Analisa a cada K candles. |
| `--max-calls` | `backtest.max_calls` | Limite de chamadas à IA. |
| `--stop` | `backtest.stop_points` | Stop em pontos, como no robô (0 = sem stop). Se o pior preço do candle atingir o stop, a saída é no stop. Saltos de preço além do stop não são simulados. |
| — | `backtest.point_value` | Valor em R$ de 1 ponto por contrato (WIN = 0.20, WDO = 10). |
| — | `backtest.volume` | Contratos por operação. |
| `--no-open` | — | Não abre o navegador (só salva o HTML). |

Com `point_value: 1.0` (padrão), o "resultado_$" é igual ao resultado em pontos.

Arquivos gerados ficam em `output/`:

- `analise_*.html` — gráfico da análise ao vivo;
- `backtest_*.html` — gráfico + métricas do backtest;
- `trades_*.csv` — operações do backtest (abre no Excel).

---

## 8. Editar o prompt

O prompt fica em `prompts/trader.md`. Edite livremente — ele é relido a cada chamada.
Estes marcadores são substituídos automaticamente:

| Marcador | Vira |
|---|---|
| `{symbol_or_alias}` | Nome do ativo |
| `{timeframe}` | Timeframe |
| `{n_bars}` | Quantidade de candles |
| `{candles}` | CSV com `idx,open,high,low,close,volume,body_pct,upper_wick_pct,lower_wick_pct,range_atr,vol_ratio` (do mais antigo para o mais recente; `idx` -1 é o último candle fechado) |
| `{atr}` | ATR(14) |
| `{ema_relation}` | Posição da EMA20 em relação à EMA50, com a distância em ATR |
| `{close_vs_ema20}` | Distância do último fechamento até a EMA20, em ATR |
| `{window_high}` / `{window_low}` | Máxima e mínima da janela |

Os indicadores usam 100 candles a mais de histórico, que a IA não vê. Por isso o backtest precisa de `bars + 100`
candles antes da primeira análise: em M5, cerca de 2 pregões a mais no período. Um período curto demais avisa o erro na tela.

Para testar estratégias diferentes, crie outro arquivo e use `--prompt prompts/meu_prompt.md`.

A resposta segue o JSON definido em `ai_trade/ai/schema.py` (`structure`, `key_levels`, `bullish_factors`,
`bearish_factors`, `invalidation`, `reasoning`, `signal`, `confidence`). Mudar o formato no prompt exige mudar esse arquivo também.

---

## 9. Problemas comuns

| Erro | Causa / solução |
|---|---|
| `Falha ao conectar no MetaTrader 5` | MT5 fechado ou não logado. Abra e faça login. |
| `Símbolo 'X' não encontrado` | Nome diferente na sua corretora. Confira na *Observação do Mercado* do MT5. |
| `Nenhum candle retornado` | Período sem histórico baixado. Abra o gráfico do ativo no MT5 e role para trás para baixar. |
| `authentication_error` / `api_key` | `.env` ausente ou chave errada. |
| Porta 8000 ocupada ao rodar `web` | Use outra: `python main.py web --port 8080`. |
| `ModuleNotFoundError` | Ambiente virtual não ativado. Rode `.venv\Scripts\activate`. |
| Gráfico não abre | Abra manualmente o caminho impresso em `Gráfico:`. Ele usa a internet para carregar o Plotly. |

---

## 10. Arquitetura (para quem for mexer no código)

```
main.py                         ponto de entrada; único lugar que escolhe as implementações
config.yaml                     parâmetros padrão
prompts/trader.md               prompt
ai_trade/
  domain.py                     Prediction, Trade, BacktestResult, Direction
  interfaces.py                 MarketDataProvider, PromptBuilder, AIPredictor, ChartRenderer
  data/mt5_provider.py          dados do MetaTrader 5
  prompt/file_prompt.py         prompt a partir de arquivo
  ai/claude_predictor.py        implementação para o Claude
  ai/gemini_predictor.py        implementação para o Google Gemini
  ai/factory.py                 registro das IAs
  services/analyzer.py          análise ao vivo
  services/backtest_service.py  busca o histórico e roda o backtest (usado pela CLI e pela web)
  robot/robot.py                robô: estados, ciclo por candle, limites de risco e comandos
  execution/paper.py            ordens simuladas
  execution/mt5_executor.py     ordens no MetaTrader 5
  data/mt5_session.py           conexão única com o MT5, compartilhada entre threads
  web/app.py                    API (FastAPI) e WebSocket do front-end
  web/jobs.py                   fila que roda os backtests em segundo plano
  web/static/index.html         front-end
  backtest/engine.py            backtest
  backtest/metrics.py           métricas
  charts/plotly_renderer.py     gráficos HTML
tests/                          testes com implementações falsas (sem MT5 e sem API)
```

O código depende das **interfaces**, não das implementações. Para adicionar outra IA (OpenAI, Gemini…):

1. Crie `ai_trade/ai/minha_ia_predictor.py` com uma classe que herde de `AIPredictor` e implemente
   `predict(prompt: str) -> Prediction`.
2. Registre em `ai_trade/ai/factory.py`, no dicionário `PREDICTORS`.
3. Rode com `--provider minha_ia` (ou mude `ai.provider` no `config.yaml`).

Nada mais no sistema muda. O mesmo vale para trocar a fonte de dados (`MarketDataProvider`) ou os gráficos (`ChartRenderer`).

---

> Aviso: ferramenta de estudo. Resultado de backtest não garante resultado futuro.
