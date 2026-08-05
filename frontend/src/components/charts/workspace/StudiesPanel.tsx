/**
 * Studies dock: volume profile, market profile (TPO) and order flow.
 *
 * Each study is a switch with its settings collapsed underneath, so the panel
 * reads as three decisions rather than forty controls. Settings appear only for
 * a study that is on.
 *
 * The order-flow section states its data dependency plainly instead of pretending
 * to have a tape: footprint needs each print classified as hitting the bid or
 * the ask, and OpenAlgo streams depth and last price rather than a classified
 * tape — so the chart classifies live ticks against the best bid/ask and builds
 * from the moment you connected. Historical bars cannot be turned into it.
 */

import { Switch } from '@/components/ui/switch'
import type { GexLevelsConfig } from '@/lib/charts/gex-levels'
import type {
  FootprintConfig,
  MarketProfileConfig,
  ProfileHover,
  VolumeProfileConfig,
} from '@/lib/charts/profiles'
import { cn } from '@/lib/utils'
import { Eyebrow, Field, TinyInput, TinySelect } from './primitives'

export interface StudiesPanelProps {
  available: boolean
  volume: VolumeProfileConfig
  market: MarketProfileConfig
  footprint: FootprintConfig
  footprintBars: number
  /** Chart timeframe label — the footprint buckets on the chart's own bars. */
  interval?: string
  hover: ProfileHover | null
  onVolume(patch: Partial<VolumeProfileConfig>): void
  onMarket(patch: Partial<MarketProfileConfig>): void
  onFootprint(patch: Partial<FootprintConfig>): void
  gex: GexLevelsConfig
  /** Quality notes from the newest snapshot, shown under the settings. */
  gexNotes?: string[]
  /** False when the charted instrument has no option chain. */
  gexAvailable?: boolean
  onGex(patch: Partial<GexLevelsConfig>): void
}

export function StudiesPanel(p: StudiesPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {!p.available && (
        <p className="border-b border-border bg-muted/40 px-3 py-2 text-[11.5px] leading-snug text-muted-foreground">
          Profiles anchor to bar times. The current chart type is movement-driven, so they stay off
          until you switch back to a time-indexed type.
        </p>
      )}

      <div className="min-h-0 flex-1 space-y-0 overflow-y-auto">
        <Section
          title="Volume profile"
          subtitle="Volume traded at each price"
          on={p.volume.enabled}
          disabled={!p.available}
          onToggle={(v) => p.onVolume({ enabled: v })}
        >
          <Field label="Built over" hint="Visible range follows the viewport">
            <TinySelect
              value={p.volume.session}
              onChange={(e) =>
                p.onVolume({ session: e.target.value as VolumeProfileConfig['session'] })
              }
            >
              <option value="visible">Visible range</option>
              <option value="day">Per day</option>
              <option value="week">Per week</option>
              <option value="month">Per month</option>
              <option value="composite">All loaded bars</option>
            </TinySelect>
          </Field>
          <Field label="Display">
            <TinySelect
              value={p.volume.displayMode}
              onChange={(e) =>
                p.onVolume({ displayMode: e.target.value as VolumeProfileConfig['displayMode'] })
              }
            >
              <option value="total">Total</option>
              <option value="buySell">Buy vs sell</option>
              <option value="delta">Delta</option>
            </TinySelect>
          </Field>
          <Field label="Row size" hint="0 = auto, scaled to the instrument">
            <TinyInput
              type="number"
              min={0}
              step={0.05}
              value={p.volume.rowSize}
              onChange={(e) => p.onVolume({ rowSize: Number(e.target.value) })}
            />
          </Field>
          <Field label="Value area" hint="Share of volume inside the band">
            <TinyInput
              type="number"
              min={0.1}
              max={1}
              step={0.05}
              value={p.volume.valueAreaPercent}
              onChange={(e) => p.onVolume({ valueAreaPercent: Number(e.target.value) })}
            />
          </Field>
          <Field label="Anchor">
            <TinySelect
              value={p.volume.side}
              onChange={(e) => p.onVolume({ side: e.target.value as VolumeProfileConfig['side'] })}
            >
              <option value="right">Right edge</option>
              <option value="left">Left edge</option>
            </TinySelect>
          </Field>
          <Field label="Width" hint="Longest bar, in pixels">
            <TinyInput
              type="number"
              min={40}
              max={400}
              step={10}
              value={p.volume.width}
              onChange={(e) => p.onVolume({ width: Number(e.target.value) })}
            />
          </Field>
          <Check
            label="Point of control"
            checked={p.volume.showPoc}
            onChange={(v) => p.onVolume({ showPoc: v })}
          />
          <Check
            label="Value area lines"
            checked={p.volume.showValueArea}
            onChange={(v) => p.onVolume({ showValueArea: v })}
          />
          <Check
            label="Shade the value area"
            checked={p.volume.highlightValueArea}
            onChange={(v) => p.onVolume({ highlightValueArea: v })}
          />
        </Section>

        <Section
          title="Market profile"
          subtitle="TPO letters, value area, initial balance"
          on={p.market.enabled}
          disabled={!p.available}
          onToggle={(v) => p.onMarket({ enabled: v })}
        >
          <Field label="Sessions">
            <TinySelect
              value={p.market.session}
              onChange={(e) =>
                p.onMarket({ session: e.target.value as MarketProfileConfig['session'] })
              }
            >
              <option value="day">Per day</option>
              <option value="week">Per week</option>
              <option value="month">Per month</option>
              <option value="composite">Whole range</option>
            </TinySelect>
          </Field>
          <Field label="Trading hours">
            <TinySelect
              value={p.market.window}
              onChange={(e) => p.onMarket({ window: e.target.value })}
            >
              <option value="all-hours">All hours</option>
              <option value="india">India 09:15–15:30</option>
              <option value="asia">Asia</option>
              <option value="london">London</option>
              <option value="new-york">New York</option>
              <option value="us-regular">US regular</option>
            </TinySelect>
          </Field>
          <Field label="Minutes per letter">
            <TinyInput
              type="number"
              min={1}
              max={240}
              step={5}
              value={p.market.blockMinutes}
              onChange={(e) => p.onMarket({ blockMinutes: Number(e.target.value) })}
            />
          </Field>
          <Field label="Row size" hint="0 = auto, scaled to the instrument">
            <TinyInput
              type="number"
              min={0}
              step={0.05}
              value={p.market.rowSize}
              onChange={(e) => p.onMarket({ rowSize: Number(e.target.value) })}
            />
          </Field>
          <Field label="Initial balance" hint="Opening periods that set the range">
            <TinyInput
              type="number"
              min={1}
              max={12}
              step={1}
              value={p.market.initialBalancePeriods}
              onChange={(e) => p.onMarket({ initialBalancePeriods: Number(e.target.value) })}
            />
          </Field>
          <Field label="Merge sessions" hint="Roll N days into one profile">
            <TinyInput
              type="number"
              min={1}
              max={30}
              step={1}
              value={p.market.compositeSessions}
              onChange={(e) => p.onMarket({ compositeSessions: Number(e.target.value) })}
            />
          </Field>
          <Field label="Blocks">
            <TinySelect
              value={p.market.blockDisplay}
              onChange={(e) =>
                p.onMarket({ blockDisplay: e.target.value as MarketProfileConfig['blockDisplay'] })
              }
            >
              <option value="auto">Auto</option>
              <option value="letters">Letters</option>
              <option value="blocks">Blocks</option>
              <option value="blocks+letters">Blocks and letters</option>
            </TinySelect>
          </Field>
          <Field label="Colour by">
            <TinySelect
              value={p.market.colorMode}
              onChange={(e) =>
                p.onMarket({ colorMode: e.target.value as MarketProfileConfig['colorMode'] })
              }
            >
              <option value="period">Period</option>
              <option value="valueArea">Value area</option>
              <option value="count">Count</option>
              <option value="volume">Volume</option>
              <option value="uniform">Uniform</option>
            </TinySelect>
          </Field>
          <Check
            label="Only the sessions on screen"
            checked={p.market.visibleOnly}
            onChange={(v) => p.onMarket({ visibleOnly: v })}
          />
          <Check
            label="Split periods into columns"
            checked={p.market.split}
            onChange={(v) => p.onMarket({ split: v })}
          />
          <Check
            label="Initial balance bracket"
            checked={p.market.showInitialBalance}
            onChange={(v) => p.onMarket({ showInitialBalance: v })}
          />
          <Check
            label="Single prints"
            checked={p.market.showSinglePrints}
            onChange={(v) => p.onMarket({ showSinglePrints: v })}
          />
          <Check
            label="Naked levels"
            checked={p.market.showNakedLevels}
            onChange={(v) => p.onMarket({ showNakedLevels: v })}
          />
          <Check
            label="Developing POC and value area"
            checked={p.market.showDevelopingPoc}
            onChange={(v) => p.onMarket({ showDevelopingPoc: v })}
          />
          <Check
            label="Volume at price"
            checked={p.market.showVolumeProfile}
            onChange={(v) => p.onMarket({ showVolumeProfile: v })}
          />
          <Check
            label="Day and open type"
            checked={p.market.showDayType}
            onChange={(v) => p.onMarket({ showDayType: v })}
          />
        </Section>

        <Section
          title="Order flow"
          subtitle="Footprint, delta and imbalances"
          on={p.footprint.enabled}
          disabled={!p.available}
          onToggle={(v) => p.onFootprint({ enabled: v })}
        >
          <p className="rounded-md border border-border bg-muted/40 px-2.5 py-2 text-[11px] leading-snug text-muted-foreground">
            Footprint needs each trade classified as hitting the bid or the ask. OpenAlgo streams
            depth and last price, so the chart classifies live ticks against the best bid/ask and
            builds from the moment you connected — history cannot be reconstructed into it.
            <span className="mt-1 block tabular-nums text-foreground/70">
              {p.footprint.enabled
                ? `${p.footprintBars} bar${p.footprintBars === 1 ? '' : 's'} built this session`
                : 'Not collecting'}
            </span>
          </p>
          <Field label="Bars built from" hint="Footprint columns line up with the candles">
            <span className="text-[11px] tabular-nums text-muted-foreground">
              Chart timeframe{p.interval ? ` (${p.interval})` : ''}
            </span>
          </Field>
          <Field label="Cell text" hint="Largest size the numbers grow to; 10 pins one size">
            <TinyInput
              type="number"
              min={10}
              max={28}
              step={1}
              value={p.footprint.maxFont}
              onChange={(e) => p.onFootprint({ maxFont: Number(e.target.value) })}
            />
          </Field>
          <Field label="Row size" hint="0 = auto, scaled to the instrument">
            <TinyInput
              type="number"
              min={0}
              step={0.05}
              value={p.footprint.rowSize}
              onChange={(e) => p.onFootprint({ rowSize: Number(e.target.value) })}
            />
          </Field>
          <Field label="Cells show">
            <TinySelect
              value={p.footprint.displayMode}
              onChange={(e) =>
                p.onFootprint({ displayMode: e.target.value as FootprintConfig['displayMode'] })
              }
            >
              <option value="bidask">Bid against ask</option>
              <option value="delta">Delta</option>
              <option value="volume">Volume</option>
            </TinySelect>
          </Field>
          <Field label="Imbalance ratio" hint="Ask over bid one tick below">
            <TinyInput
              type="number"
              min={1.2}
              step={0.5}
              value={p.footprint.imbalanceRatio}
              onChange={(e) => p.onFootprint({ imbalanceRatio: Number(e.target.value) })}
            />
          </Field>
          <Field label="Stacked run" hint="0 turns brackets off">
            <TinyInput
              type="number"
              min={0}
              step={1}
              value={p.footprint.stackedImbalances}
              onChange={(e) => p.onFootprint({ stackedImbalances: Number(e.target.value) })}
            />
          </Field>
          <Check
            label="Candle behind the cells"
            checked={p.footprint.showCandle}
            onChange={(v) => p.onFootprint({ showCandle: v })}
          />
          <Check
            label="Mark each bar's busiest row"
            checked={p.footprint.showPoc}
            onChange={(v) => p.onFootprint({ showPoc: v })}
          />
          <div className="space-y-1.5 pt-1">
            <Eyebrow>Stats under each bar</Eyebrow>
            <div className="flex flex-wrap gap-1">
              {(['volume', 'delta', 'deltaPct', 'cvd', 'trades'] as const).map((row) => {
                const on = p.footprint.statsRows.includes(row)
                return (
                  <button
                    key={row}
                    type="button"
                    onClick={() =>
                      p.onFootprint({
                        statsRows: on
                          ? p.footprint.statsRows.filter((r) => r !== row)
                          : [...p.footprint.statsRows, row],
                      })
                    }
                    className={cn(
                      'h-6 rounded-md border border-border px-2 text-[11px] transition-colors hover:bg-accent',
                      on && 'border-primary/40 bg-primary/12 text-primary'
                    )}
                  >
                    {row === 'deltaPct' ? 'delta %' : row}
                  </button>
                )
              })}
            </div>
          </div>
        </Section>

        <Section
          title="GEX levels"
          subtitle="Dealer gamma walls and the flip"
          on={p.gex.enabled}
          disabled={p.gexAvailable === false}
          onToggle={(v) => p.onGex({ enabled: v })}
        >
          {p.gexAvailable === false && (
            <p className="rounded-md border border-border bg-muted/40 px-2.5 py-2 text-[11px] leading-snug text-muted-foreground">
              GEX needs an underlying with a listed option chain. An option's own chart cannot show
              it — its price axis is premium, not the underlying's price.
            </p>
          )}
          <Field label="Metric" hint="Gamma is hedging pressure; delta is which way the book leans">
            <TinySelect
              value={p.gex.metric}
              onChange={(e) => p.onGex({ metric: e.target.value as GexLevelsConfig['metric'] })}
            >
              <option value="gamma">Gamma (GEX)</option>
              <option value="delta">Delta (DEX)</option>
            </TinySelect>
          </Field>
          <Field label="Weight by" hint="OI is the standing book; volume is today's flow">
            <TinySelect
              value={p.gex.weightBy}
              onChange={(e) => p.onGex({ weightBy: e.target.value as GexLevelsConfig['weightBy'] })}
            >
              <option value="oi">Open interest</option>
              <option value="volume">Volume</option>
            </TinySelect>
          </Field>
          <Field label="Expiry" hint="Blank uses the nearest">
            <TinyInput
              type="text"
              placeholder="Nearest"
              value={p.gex.expiry}
              onChange={(e) => p.onGex({ expiry: e.target.value.trim().toUpperCase() })}
            />
          </Field>
          <Field label="Strike bars">
            <TinySelect
              value={p.gex.showBars ? 'show' : 'levels'}
              onChange={(e) => p.onGex({ showBars: e.target.value === 'show' })}
            >
              <option value="show">Show</option>
              <option value="levels">Levels only</option>
            </TinySelect>
          </Field>
          <Field label="Readout card" hint="The numbers panel over the chart">
            <TinySelect
              value={p.gex.showDashboard ? 'show' : 'hide'}
              onChange={(e) => p.onGex({ showDashboard: e.target.value === 'show' })}
            >
              <option value="show">Show</option>
              <option value="hide">Hide</option>
            </TinySelect>
          </Field>
          <Field label="Refresh">
            <TinySelect
              value={String(p.gex.refreshSeconds)}
              onChange={(e) => p.onGex({ refreshSeconds: Number(e.target.value) })}
            >
              <option value="15">15s</option>
              <option value="30">30s</option>
              <option value="60">60s</option>
              <option value="120">120s</option>
            </TinySelect>
          </Field>
          {p.gexNotes && p.gexNotes.length > 0 && (
            <div className="space-y-0.5 pt-1">
              {p.gexNotes.map((note) => (
                <p key={note} className="text-[11px] leading-snug text-muted-foreground">
                  {note}
                </p>
              ))}
            </div>
          )}
        </Section>
      </div>

      {p.hover && (
        <div className="shrink-0 border-t border-border bg-muted/30 px-3 py-2">
          <Eyebrow className="mb-1.5">
            {p.hover.kind === 'footprint' ? 'Under the pointer' : 'Profile at the pointer'}
          </Eyebrow>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11.5px] tabular-nums">
            {p.hover.lines.map((l) => (
              <div key={l.label} className="contents">
                <dt className="truncate text-muted-foreground">{l.label}</dt>
                <dd className="truncate text-right">{l.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}

function Section({
  title,
  subtitle,
  on,
  disabled,
  onToggle,
  children,
}: {
  title: string
  subtitle: string
  on: boolean
  disabled?: boolean
  onToggle(on: boolean): void
  children: React.ReactNode
}) {
  return (
    <section className="border-b border-border">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[13px] font-semibold">{title}</h3>
          <p className="truncate text-[11px] text-muted-foreground">{subtitle}</p>
        </div>
        <Switch checked={on} disabled={disabled} onCheckedChange={onToggle} />
      </div>
      {on && <div className="space-y-2.5 border-t border-border/60 px-3 py-3">{children}</div>}
    </section>
  )
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange(v: boolean): void
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-[12px] text-foreground/85">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3.5 w-3.5 accent-primary"
      />
      {label}
    </label>
  )
}
