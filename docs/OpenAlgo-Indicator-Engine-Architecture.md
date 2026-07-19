# Recommended OpenAlgo OpenScript (Indicator Engine) Architecture

Do **not** build the indicator engine entirely inside React, and do not let custom scripts execute through JavaScript `eval()` or arbitrary Python.

Build a four-layer indicator platform:

1. **OpenAlgo Charts** — rendering only.
2. **OpenAlgo Indicator Core** — mathematical calculations.
3. **OpenScript Runtime** — Pine-like custom indicator language.
4. **Server Execution Service** — alerts, scanners, persistence, and headless execution.

The strongest implementation path is to reuse OpenAlgo’s existing Rust indicator core for both frontend and backend:

* **Browser:** compile the Rust core to WebAssembly.
* **Backend:** continue using the same Rust core through the existing PyO3-based `openalgo.ta`.
* **Custom scripts:** compile Pine-like source into a safe, language-neutral Intermediate Representation, or IR, that invokes the same Rust indicator functions.

This prevents calculation differences between charts, alerts, Python strategies, scanners, and backtests.

---

## 1. Why the existing repositories are already suitable

Your OpenAlgo frontend already includes:

* React 19 and TypeScript.
* CodeMirror.
* Zustand.
* WebSocket support.
* `openalgo-charts` version `1.0.6`.
* Lightweight Charts, which can remain for legacy pages during migration.

`openalgo-charts` already provides the rendering capabilities required by an indicator platform:

* Multiple panes.
* Overlay and sub-pane series.
* Line, histogram, column, candle, area, and other series.
* Horizontal price lines.
* Buy/sell markers.
* Custom canvas primitives.
* Chart-type registration.
* Historical REST and live WebSocket feed adapters.

The series API supports full replacement, historical prepend, and incremental live updates. It also exposes marker creation and independent price scales.

The chart API supports dynamically created panes, primitives, price lines, markers, and event subscriptions.

Most importantly, OpenAlgo already depends on `openalgo==2.0.3`.  The OpenAlgo Python SDK contains more than 100 indicators backed by a Rust core through PyO3.

That Rust crate is deliberately zero-dependency and preserves numerical behavior, including seeding, accumulation order, and `NaN` warm-up regions.

That is the correct calculation kernel for the new frontend engine.

---

# 2. Target architecture

```text
┌───────────────────────────────────────────────────────────────┐
│                    React Chart Workspace                       │
│                                                               │
│  Symbol | Timeframe | Indicators | Templates | Alerts | Code  │
└───────────────────────────────┬───────────────────────────────┘
                                │
             ┌──────────────────┴──────────────────┐
             │                                     │
┌────────────▼────────────┐            ┌───────────▼────────────┐
│ Market Data Controller │            │ Indicator Manager       │
│                        │            │                          │
│ REST history           │            │ Active instances        │
│ WebSocket ticks        │            │ Input configuration      │
│ Candle aggregation     │            │ Dependency graph         │
│ MTF datasets           │            │ Output lifecycle         │
└────────────┬────────────┘            └───────────┬─────────────┘
             │                                     │
             └──────────────────┬──────────────────┘
                                │ OHLCV datasets
                     ┌──────────▼───────────┐
                     │ Indicator Web Worker │
                     │                      │
                     │ OpenScript parser    │
                     │ Type checker         │
                     │ IR executor          │
                     │ Rust/WASM TA core    │
                     │ Runtime state        │
                     └──────────┬───────────┘
                                │ Render outputs
                     ┌──────────▼───────────┐
                     │ Chart Render Adapter │
                     │                      │
                     │ plot → series        │
                     │ hline → price line   │
                     │ shape → markers      │
                     │ fill → primitive     │
                     │ barcolor → candles   │
                     └──────────┬───────────┘
                                │
                     ┌──────────▼───────────┐
                     │  openalgo-charts     │
                     └──────────────────────┘
```

Server side:

```text
Flask Indicator APIs
        │
        ├── Script CRUD and versioning
        ├── Compilation and validation
        ├── Chart layouts and templates
        ├── Alert definitions
        └── Headless execution
                 │
                 ▼
       OpenScript Server Runtime
                 │
                 ▼
       openalgo.ta / Rust oa_core
```

---

# 3. Separate rendering from execution

`openalgo-charts` must remain a renderer and interaction engine. It should not become responsible for parsing scripts, maintaining historical buffers, or evaluating expressions.

Use three contracts.

## Calculation contract

```ts
export interface IndicatorDataset {
  time: Float64Array
  open: Float64Array
  high: Float64Array
  low: Float64Array
  close: Float64Array
  volume: Float64Array
}

export interface IndicatorExecutionContext {
  symbol: string
  exchange: string
  timeframe: string
  dataset: IndicatorDataset
  inputs: Record<string, unknown>
  executionMode: 'historical' | 'realtime' | 'bar-close'
  currentBarIndex: number
}
```

## Output contract

```ts
export type IndicatorOutput =
  | LineOutput
  | HistogramOutput
  | CandleOutput
  | MarkerOutput
  | HorizontalLineOutput
  | FillOutput
  | BarColorOutput
  | AlertOutput

export interface LineOutput {
  kind: 'line'
  id: string
  title: string
  pane: 'overlay' | number
  values: Float64Array
  style: {
    color: string
    lineWidth: number
  }
}
```

## Renderer contract

```ts
export interface IndicatorRenderer {
  attach(output: IndicatorOutput): void
  replace(output: IndicatorOutput): void
  update(output: IndicatorOutput, barIndex: number): void
  remove(outputId: string): void
  dispose(): void
}
```

This allows the same execution engine to render through OpenAlgo Charts today and potentially support exports, scanner tables, or mobile clients later.

---

# 4. Use the existing Rust core for default indicators

The current chart package includes only EMA, RSI, ATR, and Supertrend helpers. Those helpers produce plottable bars and demonstrate the extension model.

The Python SDK, however, already has more than 100 indicators, including SMA, EMA, Supertrend, RSI, MACD, ATR, and Bollinger Bands.

Therefore:

## Add a WASM wrapper crate

Add this alongside the existing Rust crates:

```text
openalgo-python-library/
└── rust/
    ├── oa_core/          # Existing shared indicator calculations
    ├── oa_py/            # Existing PyO3 wrapper
    └── oa_wasm/          # New wasm-bindgen wrapper
```

Conceptual `Cargo.toml`:

```toml
[package]
name = "oa_wasm"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib", "rlib"]

[dependencies]
oa_core = { path = "../oa_core" }
wasm-bindgen = "0.2"
js-sys = "0.3"
```

The mathematical implementation remains in `oa_core`; only the data conversion wrapper is browser-specific.

## Generated frontend package

Publish or locally consume:

```text
@openalgo/indicators-wasm
```

Example usage:

```ts
import init, { ema, rsi, macd } from '@openalgo/indicators-wasm'

await init()

const ema20 = ema(closeValues, 20)
const rsi14 = rsi(closeValues, 14)
const [macdLine, signalLine, histogram] = macd(
  closeValues,
  12,
  26,
  9
)
```

## Benefits

* The browser chart and Python strategy produce the same values.
* Indicator seeding remains consistent.
* Warm-up values remain consistent.
* No need to maintain separate TypeScript and Python formulas.
* New indicators automatically become available to both runtimes after wrapper registration.
* Calculation executes off the React thread through a Web Worker.

The current Rust implementation is especially suitable for this because the core works on slices and returns vectors without depending on NumPy, Python, or browser APIs.

---

# 5. Default indicator registry

Every built-in indicator should have declarative metadata rather than a custom React component.

```ts
export interface IndicatorDefinition {
  id: string
  version: number
  name: string
  shortName: string
  category:
    | 'trend'
    | 'momentum'
    | 'volatility'
    | 'volume'
    | 'oscillator'
    | 'statistical'
    | 'custom'

  overlay: boolean
  minimumBars: number

  inputs: IndicatorInputDefinition[]
  outputs: IndicatorOutputDefinition[]

  execute: (
    context: IndicatorExecutionContext
  ) => Promise<IndicatorOutput[]>
}
```

Example:

```ts
export const emaDefinition: IndicatorDefinition = {
  id: 'builtin.ema',
  version: 1,
  name: 'Exponential Moving Average',
  shortName: 'EMA',
  category: 'trend',
  overlay: true,
  minimumBars: 1,

  inputs: [
    {
      id: 'source',
      type: 'source',
      label: 'Source',
      defaultValue: 'close',
    },
    {
      id: 'period',
      type: 'integer',
      label: 'Length',
      defaultValue: 20,
      min: 1,
      max: 10000,
    },
  ],

  outputs: [
    {
      id: 'ema',
      type: 'line',
      pane: 'overlay',
      defaultStyle: {
        color: '#2962ff',
        lineWidth: 2,
      },
    },
  ],

  async execute(ctx) {
    const source = resolveSource(ctx.dataset, ctx.inputs.source)
    const values = wasmIndicators.ema(source, Number(ctx.inputs.period))

    return [
      {
        kind: 'line',
        id: 'ema',
        title: `EMA ${ctx.inputs.period}`,
        pane: 'overlay',
        values,
        style: {
          color: '#2962ff',
          lineWidth: 2,
        },
      },
    ]
  },
}
```

Suggested first registry:

* SMA, EMA, WMA, HMA.
* VWAP and VWMA.
* Bollinger Bands.
* Supertrend.
* ATR.
* RSI.
* MACD.
* Stochastic.
* ADX/DMI.
* CCI.
* ROC.
* OBV.
* MFI.
* Ichimoku.
* Donchian Channels.
* Pivot Points and CPR.

Do not manually code their settings forms. Generate forms from the input metadata.

---

# 6. Build a Pine-like language, not unrestricted JavaScript

Call the language something independent, such as:

* **OpenScript**
* **AlgoScript**
* **OpenAlgo Script**

“OpenScript” is used below.

Example:

```text
//@version=1
indicator("EMA Crossover", overlay=true)

fastLength = input.int(9, "Fast EMA", minval=1)
slowLength = input.int(21, "Slow EMA", minval=1)

fast = ta.ema(close, fastLength)
slow = ta.ema(close, slowLength)

bullish = ta.crossover(fast, slow)
bearish = ta.crossunder(fast, slow)

plot(fast, "Fast EMA", color=color.green, linewidth=2)
plot(slow, "Slow EMA", color=color.red, linewidth=2)

plotshape(
    bullish,
    title="Buy",
    location=location.belowbar,
    shape=shape.arrowup,
    color=color.green,
    text="BUY"
)

plotshape(
    bearish,
    title="Sell",
    location=location.abovebar,
    shape=shape.arrowdown,
    color=color.red,
    text="SELL"
)

alertcondition(bullish, "EMA Buy", "Fast EMA crossed above Slow EMA")
alertcondition(bearish, "EMA Sell", "Fast EMA crossed below Slow EMA")
```

Do not initially promise complete Pine Script compatibility. Start with an intentionally bounded language.

## OpenScript v1 scope

Support:

* `indicator()`.
* `input.int`, `input.float`, `input.bool`, `input.string`, `input.source`.
* Primitive values and series values.
* Arithmetic and boolean expressions.
* `if` expressions.
* Historical references such as `close[1]`.
* `na`, `nz()`.
* `math.*`.
* `ta.*`.
* `plot`.
* `hline`.
* `fill`.
* `plotshape`.
* `plotchar`.
* `barcolor`.
* `bgcolor`.
* `alertcondition`.
* Non-recursive user-defined functions.

Defer initially:

* Strategies and order simulation.
* Arbitrary loops.
* Arrays, maps, matrices and objects.
* Dynamic symbol requests.
* `request.security`.
* Drawing-object mutation such as unrestricted `line.new`.
* Imports and community libraries.
* Arbitrary network calls.
* Filesystem or browser access.

---

# 7. Compiler pipeline

```text
OpenScript source
       │
       ▼
Lexer / tokenizer
       │
       ▼
Parser
       │
       ▼
Abstract Syntax Tree
       │
       ▼
Semantic analysis
  - symbol resolution
  - input validation
  - series/simple/const typing
  - scope checks
  - plot declaration checks
       │
       ▼
Typed Intermediate Representation
       │
       ▼
Execution plan / dependency graph
       │
       ▼
Runtime evaluator
       │
       ├── oa_core WASM functions
       ├── scalar operators
       ├── historical buffers
       └── output collectors
```

Never transpile user code to JavaScript and run it through:

```ts
eval(source)
new Function(source)
```

A custom AST interpreter provides:

* Predictable execution.
* Operation limits.
* Memory limits.
* Deterministic behavior.
* No DOM access.
* No cookies or local storage access.
* No network access.
* Better compile errors with exact source locations.

---

# 8. Intermediate Representation

The compiler should emit JSON-compatible IR.

```json
{
  "version": 1,
  "declaration": {
    "name": "EMA Crossover",
    "overlay": true
  },
  "inputs": [
    {
      "id": "fastLength",
      "type": "integer",
      "defaultValue": 9,
      "min": 1
    }
  ],
  "nodes": [
    {
      "id": 1,
      "op": "source",
      "source": "close"
    },
    {
      "id": 2,
      "op": "input",
      "inputId": "fastLength"
    },
    {
      "id": 3,
      "op": "call",
      "namespace": "ta",
      "function": "ema",
      "args": [1, 2]
    }
  ],
  "outputs": [
    {
      "kind": "plot",
      "nodeId": 3,
      "title": "Fast EMA",
      "style": {
        "color": "#00a000",
        "lineWidth": 2
      }
    }
  ]
}
```

The server must compile the source again or cryptographically verify the compiled IR. Never trust IR submitted directly by the browser.

---

# 9. Pine-style execution semantics

A Pine-like runtime is not simply “run a function over an array.” It must define how state evolves across bars.

TradingView’s runtime executes scripts sequentially across historical bars. On a realtime bar, indicators recalculate on updates, roll temporary state back to the previous committed bar, and commit the final state when the bar closes. ([TradingView][1])

OpenAlgo should reproduce that model.

## Historical execution

For every bar:

```text
1. Set current OHLCV built-ins.
2. Evaluate the script.
3. Store each series result at current bar index.
4. Commit persistent variables.
5. Advance to the next bar.
```

## Realtime execution

Maintain two states:

```ts
interface RuntimeState {
  committed: RuntimeSnapshot
  working: RuntimeSnapshot
}
```

On a tick updating the open candle:

```text
1. Restore working state from committed state.
2. Replace current open-bar OHLCV values.
3. Execute the current bar.
4. Send temporary output updates to the chart.
5. Do not commit.
```

When the candle closes:

```text
1. Execute with final OHLCV.
2. Commit working state.
3. Append all series values.
4. Begin the next candle.
```

This is necessary to prevent:

* Intrabar counters accumulating incorrectly.
* Markers remaining after their condition becomes false.
* Repainting artifacts.
* Alerts being emitted multiple times from temporary ticks.
* Divergence between live and refreshed historical charts.

## Execution modes

Support explicit modes:

```ts
type ExecutionMode =
  | 'once-per-bar-close'
  | 'on-every-tick'
  | 'historical'
```

Default custom indicators to `on-every-tick` for visual updates, but default alert triggering to confirmed bar close unless the user explicitly enables intrabar alerts.

---

# 10. Mapping OpenScript outputs to OpenAlgo Charts

| OpenScript output  | OpenAlgo Charts implementation                 |
| ------------------ | ---------------------------------------------- |
| `plot()`           | `chart.addSeries('line')`                      |
| Histogram plot     | `chart.addSeries('histogram')`                 |
| Column plot        | `chart.addSeries('column')`                    |
| Candle output      | `chart.addSeries('candlestick')`               |
| Sub-pane indicator | `paneIndex: 1+`                                |
| `hline()`          | `chart.addPriceLine()`                         |
| `plotshape()`      | `series.createMarkers()`                       |
| `plotchar()`       | Text marker                                    |
| `fill()`           | Custom band primitive                          |
| `bgcolor()`        | Background-region primitive                    |
| `barcolor()`       | Candle style override or colored candle series |
| `alertcondition()` | Runtime alert output                           |
| Custom drawing     | `IPrimitive` implementation                    |

Markers already support arrows, triangles, circles, squares, diamonds, flags, text, above-bar, below-bar, in-bar, and fixed-price positioning.

The primitive extension interface supports drawing, autoscale contribution, hit testing, attachment lifecycle, and layered z-order.

Therefore, you do not need to modify the chart core for every indicator visual.

---

# 11. Incremental calculations

Do not recalculate 50,000 candles on every tick.

Each execution node should declare its calculation characteristics:

```ts
interface RuntimeOperationDefinition {
  id: string
  warmupBars: number
  incremental: boolean
  recalculateFrom: (
    change: DatasetChange
  ) => number
}
```

Examples:

| Indicator      | Recalculation requirement                        |
| -------------- | ------------------------------------------------ |
| EMA            | Previous EMA plus current close                  |
| RSI            | Previous smoothed gain/loss plus current change  |
| ATR            | Previous ATR plus current true range             |
| SMA            | Rolling sum and outgoing value                   |
| Highest/Lowest | Monotonic queue or bounded window                |
| VWAP           | Session cumulative price-volume state            |
| Pivot Points   | Recalculate on session boundary                  |
| MTF indicator  | Recalculate when source timeframe candle changes |

Use two execution paths:

### Initial historical load

Call the vectorized Rust functions over all bars.

### Live updates

Use stateful incremental kernels for the current bar and new bars.

For the first release, it is acceptable to recalculate only a bounded tail, for example:

```text
max(warmupBars × 3, 500 bars)
```

Then add native incremental kernels progressively.

---

# 12. Worker-based frontend execution

Run compilation and calculation inside a dedicated Web Worker.

```text
frontend/src/features/charting/openscript/
├── worker/
│   ├── indicator.worker.ts
│   ├── protocol.ts
│   └── worker-client.ts
├── compiler/
│   ├── lexer.ts
│   ├── parser.ts
│   ├── ast.ts
│   ├── semantic-analyzer.ts
│   └── ir-generator.ts
├── runtime/
│   ├── runtime.ts
│   ├── execution-context.ts
│   ├── series-buffer.ts
│   ├── rollback-state.ts
│   └── operation-budget.ts
├── registry/
│   ├── definitions.ts
│   ├── builtins.ts
│   └── generated-manifest.ts
├── render/
│   ├── openalgo-chart-adapter.ts
│   ├── output-controller.ts
│   ├── band-primitive.ts
│   └── background-primitive.ts
└── wasm/
    └── indicator-core.ts
```

Worker message contract:

```ts
export type IndicatorWorkerRequest =
  | {
      type: 'compile'
      requestId: string
      source: string
    }
  | {
      type: 'execute-history'
      requestId: string
      program: CompiledProgram
      dataset: TransferableDataset
      inputs: Record<string, unknown>
    }
  | {
      type: 'update-bar'
      requestId: string
      sessionId: string
      bar: OHLCVBar
      confirmed: boolean
    }
  | {
      type: 'dispose-session'
      sessionId: string
    }
```

Use transferable `ArrayBuffer` objects rather than cloning large JavaScript arrays between the main thread and worker.

---

# 13. Frontend chart workspace

Create a production route such as:

```text
/charts
```

Recommended layout:

```text
┌──────────────────────────────────────────────────────────────────┐
│ Symbol  Exchange  Timeframe  Indicators  Templates  Alerts       │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                       Price chart                                │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                       RSI / MACD pane                             │
├──────────────────────────────────────────────────────────────────┤
│ Status | Data count | Execution time | Realtime connection       │
└──────────────────────────────────────────────────────────────────┘
```

Editor mode:

```text
┌─────────────────────────────┬────────────────────────────────────┐
│ OpenScript CodeMirror       │ Live chart preview                 │
│                             │                                    │
│ Compile errors              │ Data window                        │
│ Inputs                      │ Indicator values                   │
│ Console                     │ Alerts                             │
└─────────────────────────────┴────────────────────────────────────┘
```

Pages/components:

```text
frontend/src/pages/charts/
├── ChartWorkspace.tsx
├── IndicatorLibrary.tsx
├── IndicatorEditor.tsx
├── SavedIndicators.tsx
└── IndicatorDocumentation.tsx
```

The existing `/chart/test` page already demonstrates symbol search, historical loading, shared market-data subscription, live candle construction, and timeframe switching.  It should be treated as a prototype source and replaced by the production workspace rather than extended indefinitely.

---

# 14. Market-data flow

Use one authoritative bar store per:

```text
exchange + symbol + timeframe
```

```ts
type DatasetKey = `${string}:${string}:${string}`
```

Pipeline:

```text
History REST
    │
    ▼
Normalized UTC OHLCV bars
    │
    ▼
Dataset cache
    │
    ├── Price series
    ├── Default indicators
    ├── Custom indicators
    └── Alert runtime
```

For live data:

```text
OpenAlgo WebSocket tick
    │
    ▼
Candle builder
    │
    ▼
Current candle replace / new candle append
    │
    ▼
Indicator runtime update
    │
    ▼
Series.update()
```

`openalgo-charts` already has a composed live feed that combines REST history, WebSocket LTP, and a per-subscription candle builder.  It also supports seeding the candle builder with the latest historical candle so the open bar continues correctly.

Either use that feed directly or adapt the existing shared `MarketDataManager` to the same `DataFeed` contract. Do not operate two independent WebSocket connections for the same workspace.

---

# 15. Backend services

Recommended backend structure:

```text
blueprints/
└── indicators.py

services/
└── openscript/
    ├── compiler_service.py
    ├── execution_service.py
    ├── runtime_session_service.py
    ├── alert_service.py
    ├── dataset_service.py
    ├── manifest_service.py
    └── limits.py

database/
└── indicator_db.py
```

## APIs

```text
GET    /indicators/api/catalog
GET    /indicators/api/catalog/{indicator_id}

GET    /indicators/api/scripts
POST   /indicators/api/scripts
GET    /indicators/api/scripts/{script_id}
PUT    /indicators/api/scripts/{script_id}
DELETE /indicators/api/scripts/{script_id}

POST   /indicators/api/compile
POST   /indicators/api/execute
POST   /indicators/api/validate

GET    /indicators/api/layouts
POST   /indicators/api/layouts
PUT    /indicators/api/layouts/{layout_id}
DELETE /indicators/api/layouts/{layout_id}

GET    /indicators/api/alerts
POST   /indicators/api/alerts
PUT    /indicators/api/alerts/{alert_id}
DELETE /indicators/api/alerts/{alert_id}
```

Example compile response:

```json
{
  "status": "success",
  "compilerVersion": "openscript-1.0",
  "sourceHash": "sha256:...",
  "program": {
    "inputs": [],
    "outputs": [],
    "ir": {}
  },
  "warnings": [
    {
      "code": "OS1004",
      "line": 12,
      "column": 5,
      "message": "This expression requires 20 warm-up bars"
    }
  ]
}
```

---

# 16. Database model

## `indicator_scripts`

```text
id
user_id
name
description
language
current_version_id
visibility
created_at
updated_at
```

Visibility:

```text
private
shared
public
builtin
```

## `indicator_script_versions`

```text
id
script_id
version_number
source_code
source_hash
compiler_version
compiled_ir
metadata_json
created_at
```

Never overwrite historical source versions.

## `chart_layouts`

```text
id
user_id
name
symbol
exchange
timeframe
layout_json
created_at
updated_at
```

`layout_json` stores:

* Active indicators.
* Indicator version IDs.
* Inputs.
* Styles.
* Pane positions and sizes.
* Visible/hidden state.
* Price-scale selection.

## `indicator_alerts`

```text
id
user_id
script_version_id
symbol
exchange
timeframe
condition_id
inputs_json
trigger_mode
is_active
last_evaluated_bar
last_triggered_at
created_at
updated_at
```

## `indicator_execution_errors`

```text
id
user_id
script_version_id
symbol
timeframe
phase
error_code
message
bar_index
created_at
```

---

# 17. Security limits

Every custom script needs deterministic limits.

Suggested defaults:

```ts
export const SCRIPT_LIMITS = {
  maximumSourceBytes: 100_000,
  maximumAstNodes: 10_000,
  maximumOutputs: 64,
  maximumInputs: 100,
  maximumVariables: 2_000,
  maximumFunctionDepth: 32,
  maximumHistoryBars: 100_000,
  maximumLookback: 20_000,
  maximumOperationsPerBar: 100_000,
  maximumTotalOperations: 100_000_000,
  maximumExecutionMilliseconds: 2_000,
  maximumWorkerMemoryMb: 256,
}
```

The first version should prohibit:

* Recursive functions.
* Infinite loops.
* Dynamic code execution.
* Browser globals.
* DOM access.
* HTTP requests.
* WebSockets.
* Files.
* Environment variables.
* Arbitrary Python imports.
* Direct order placement.

Indicator scripts should generate **signals**, not place orders directly. A separate strategy or alert action layer can consume those signals with OpenAlgo’s analyzer and approval controls.

TradingView similarly imposes script limits around execution time, memory, source size, and external data requests. ([TradingView][2])

---

# 18. PineTS: where it fits

PineTS can provide a faster Pine import path. Its current package offers browser and Node execution, Pine syntax support, time-series handling, realtime streaming, and many technical-analysis functions. However, it remains pre-1.0 and is licensed under AGPL-3.0 with a separate commercial licensing option. ([npm][3]) ([npm][4])

The existing `openalgo-pinets` repository is useful as a reference implementation. It demonstrates one indicator, Williams VIX Fix, using OpenAlgo market data and PineTS-style calculations.  Its integration model exposes `data`, `ta`, and `plot` contexts.

My recommendation:

### Do not make PineTS the canonical default-indicator engine

Reasons:

* It would duplicate OpenAlgo’s existing Rust indicator calculations.
* Python strategies and frontend charts could produce different values.
* It introduces an external compatibility and licensing dependency.
* Pine behavior changes would be controlled outside OpenAlgo.
* It is not necessary for native OpenAlgo indicators.

### Use PineTS as an optional import adapter

```text
Pine Script source
       │
       ▼
PineTS compatibility layer
       │
       ▼
Normalized IndicatorOutput[]
       │
       ▼
OpenAlgo Charts renderer
```

This can be presented as:

```text
Experimental Pine Import
```

The stable native path should remain:

```text
OpenScript → OpenAlgo IR → OpenAlgo Rust Core
```

---

# 19. Implementation phases

## Phase 0 — Production chart foundation

* Add `/charts`.
* Replace Lightweight Charts with `openalgo-charts` on this new route.
* Add symbol search, timeframe selection, history, and live bar updates.
* Introduce a shared `ChartDataController`.
* Support price and volume.
* Preserve zoom during live updates.
* Normalize all timestamps to UTC internally.

**Exit criteria:** price and volume render correctly for historical and live bars without indicators.

## Phase 1 — Default indicator engine

* Create the indicator definition registry.
* Add input-generated settings panels.
* Implement output-to-chart adapter.
* Create `oa_wasm`.
* Add 10–15 core indicators.
* Run all calculations inside a Web Worker.
* Add hide, edit, remove, reorder, and pane management.
* Persist chart layouts.

**Exit criteria:** frontend WASM results match `openalgo.ta` fixtures for every supported indicator.

## Phase 2 — OpenScript compiler

* Lexer and parser.
* AST.
* Type system.
* Historical series references.
* Inputs.
* `ta.*`, `math.*`.
* Plot outputs.
* Compile diagnostics.
* CodeMirror language highlighting.
* Save and version custom indicators.

**Exit criteria:** users can create EMA-cross, RSI, MACD, Supertrend, candle-pattern, and marker-based scripts.

## Phase 3 — Realtime execution and rollback

* Working versus committed state.
* Intrabar recalculation.
* Confirmed-bar commit.
* Marker rollback.
* Alert-condition collection.
* Execution profiling.
* Operation limits.

**Exit criteria:** live results equal a full page refresh after each candle closes.

## Phase 4 — Server execution and alerts

* Server-side IR validation.
* Headless dataset execution.
* Alert persistence.
* Scheduler and market session handling.
* WebSocket or Socket.IO alert delivery.
* Analyzer integration.
* Execution logs.

**Exit criteria:** alerts continue running with the chart page closed.

## Phase 5 — Advanced compatibility

* MTF datasets.
* `request.security` equivalent.
* Indicator-on-indicator sources.
* Custom libraries.
* Controlled loops and arrays.
* PineTS import adapter.
* Community indicator library.
* Scanner integration.

---

# 20. Suggested PR sequence

1. **Chart Workspace and OpenAlgo Charts integration**
2. **Normalized historical/live data controller**
3. **Indicator output contracts and chart adapter**
4. **Rust `oa_core` WASM wrapper**
5. **Built-in indicator registry and settings UI**
6. **Web Worker calculation runtime**
7. **Chart-layout persistence**
8. **OpenScript lexer, parser, and AST**
9. **OpenScript semantic analysis and IR**
10. **OpenScript historical executor**
11. **Realtime rollback and commit semantics**
12. **Custom indicator editor and versioning**
13. **Server-side execution and alert engine**
14. **MTF and Pine import compatibility**

---

# Final architectural decision

The best long-term design is:

```text
                    ONE MATHEMATICAL CORE
                           oa_core
                        Rust functions
                     /                \
                    /                  \
          PyO3 / openalgo.ta         WASM
                 │                     │
       Flask alerts/scanners       Browser charts
                 │                     │
                 └──── OpenScript IR ──┘
                            │
                            ▼
                    openalgo-charts
```

This gives OpenAlgo something stronger than a frontend-only Pine clone:

* One calculation source of truth.
* Default and custom indicators.
* Browser preview.
* Headless alerts and scanners.
* Numerical parity with Python strategies.
* Safe execution without arbitrary code.
* Incremental realtime updates.
* A Pine-like user experience without making the chart renderer responsible for script execution.

[1]: https://www.tradingview.com/pine-script-docs/language/execution-model/?utm_source=chatgpt.com "Language / Execution model"
[2]: https://www.tradingview.com/pine-script-docs/writing/limitations/?utm_source=chatgpt.com "Writing / Limitations"
[3]: https://www.npmjs.com/package/pinets?utm_source=chatgpt.com "pinets - npm"
[4]: https://www.npmjs.com/package/pinets?activeTab=dependents&utm_source=chatgpt.com "pinets - npm"
