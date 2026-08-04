# MBZ Entry Model by NINE — Technical Specification

- **Script**: `pine/MBZ_Entry_Model.pine`
- **Pine version**: v6
- **Type**: `overlay` indicator (no strategy/backtest logic, visual-only)
- **Audience**: developers maintaining, extending, or reimplementing this indicator

This document describes the exact rules, state machine, and architecture implemented
by the script, so it can be understood, audited, or reproduced without reverse-engineering
the code line by line. Where a rule has a non-obvious rationale (established through
iteration and explicit corrections), that rationale is called out — several earlier,
plausible-looking alternatives were tried and explicitly rejected; they are noted so
they aren't reintroduced by accident.

## 1. Concept Overview

The indicator implements an ICT-style "Fair Value Gap → Median Body Zone" (MBZ) entry
model:

1. A **Fair Value Gap (FVG)** forms — a classic 3-candle, wick-based imbalance.
2. Price returns to the FVG and a candle **opposing the FVG's own direction** touches
   it — this candle is **C1**.
3. The very next candle, if it closes back in the FVG's original direction and beyond
   the midpoint of C1's body, is **C2** — this **confirms** the setup and fires the
   entry.
4. **C3** is the candle after C2 — purely a cosmetic label in this implementation (see
   §7, "C3 has no functional role").
5. Entry is placed mechanically at the 0.5 (median of open/close) of C2's body, with
   SL/TP reference levels expressed as multiples of C2's own body height.

Two independent instances of this state machine run every bar: one for bullish FVGs
(→ long setups) and one for bearish FVGs (→ short setups). They do not interact except
through shared FVG registration gating (§5) and a shared debug table (§10).

## 2. FVG Detection

```
bullFvg = low > high[2]
bearFvg = high < low[2]
```

- Classic 3-candle wick-based gap, evaluated on the chart's own timeframe only (no
  multi-timeframe FVG detection).
- A **bullish FVG** zone is `top = low` (current bar), `bot = high[2]` (2 bars back).
- A **bearish FVG** zone is `top = low[2]`, `bot = high` (current bar).
- `top` is always the numerically higher price boundary and `bot` the lower one, for
  **both** directions — this symmetry is relied on elsewhere (e.g. FVG-text vertical
  alignment, §9).
- **Every** non-mitigated FVG is kept, not just the newest. They are stored in two
  growing arrays, `bullFvgs` / `bearFvgs` (`array<FvgZone>`), so a candle can still
  touch and trigger an older, still-valid gap, not only the most recent one.
- A newly detected FVG is only pushed to its array if that direction is currently
  enabled (§5, direction filter) — a disabled direction never accumulates zones at all.

## 3. The `FvgZone` State Machine

Each element of `bullFvgs`/`bearFvgs` is an `FvgZone` (fields below). A zone's
lifecycle, independent of all other zones:

```
type FvgZone
    float top, bot          // zone boundaries (top = higher price, both directions)
    int   leftBar            // formation-leg start bar, box's left edge
    int   formBar             // the bar the FVG's 3rd (confirming) candle closed on
    bool  used                 // this zone has produced at least one confirmed setup
    bool  swept                 // a swing point since the last `used` has been swept
    float lastSwing               // the swing price being tracked for the sweep check
    box   boxRef                    // this zone's visible FVG box, or na if not shown
    bool  c1Set                      // this zone currently has an unresolved C1
    float c1Open, c1Close             // C1 candle's open/close
    int   c1Bar                        // C1 candle's bar_index
    line  c1Line                        // dotted 0.5-of-C1-body validation threshold
    label c1PendingLabel                 // "C1" tag while unconfirmed
```

**Per-zone independence is the central architectural decision.** Earlier versions
tracked only a single "current C1" per direction globally; this let a newer zone's
touch silently steal the C1 opportunity from an older zone touched on the same bar,
verified against real OHLC data that mathematically satisfied every condition for a
zone that nonetheless never registered. Every zone now owns its full C1→C2 pending
state, so any number of zones can independently be "in the running" at once.

### 3.1 C1 registration (new pending C1)

Runs every bar, gated by candle direction (opposing the FVG's own bias):

```
// long setup (bullish FVG), C1 = bearish candle:
if close < open
    for z in bullFvgs
        if bar_index > z.formBar and (not z.used or z.swept) and low <= z.top and high >= z.bot
            // register z.c1Open/c1Close/c1Bar/c1Set, draw dotted 0.5 line + "C1" label

// short setup (bearish FVG), C1 = bullish candle: mirrored, high>=bot and low<=top
```

Rules:
- **Touch is wick-based**: `low <= z.top and high >= z.bot` — even a 1-tick overlap
  qualifies, not just a close inside the zone. (Earlier: required `close` strictly
  inside the zone; loosened after real-world cases showed valid touches being missed.)
- **Must be strictly after the zone's own formation bar** (`bar_index > z.formBar`) —
  a candle can never be C1 for the very FVG it just formed.
- **Reuse gate**: `not z.used or z.swept`. A zone that has never produced a confirmed
  setup (`used = false`) accepts a C1 immediately. A zone that already has (`used =
  true`) only accepts a **new** C1 once `swept` becomes true again (§4).
- **No "winner takes all"**: the loop has no `break`. Every qualifying zone in the
  array gets its own C1 registered on the same bar, independently. (Earlier version
  used newest-first iteration with `break` on first match — this is exactly the bug
  described in §3 above.)
- Registering a new C1 on a zone **overwrites** any C1 it already had pending
  (deletes the old dotted line/"C1" label first).
- The dotted C1 threshold line (`z.c1Line`) is extended one bar to the right every bar
  while `c1Set` is true, purely visual.

### 3.2 C2 validation (confirmation)

Runs every bar, over every zone with a pending C1 whose very next bar has arrived:

```
for z in bullFvgs   // long
    if z.c1Set and bar_index == z.c1Bar + 1 and barstate.isconfirmed
        validated = close > open and close > median(z.c1Open, z.c1Close)
        ...
```
(short setup mirrors with `close < open and close < median(...)`)

Rules:
- **C2 must be the bar immediately after C1** (`bar_index == z.c1Bar + 1`) — there is
  no "wait for a later trigger candle" mechanism. This was explicitly tried (a
  3rd-candle trigger/invalidation scheme) and explicitly reverted per direct user
  correction: the model wants the entry fired mechanically on C2 alone, nothing more.
- **Only once the candle has closed** (`barstate.isconfirmed`): validation is not
  attempted against a live/still-forming bar. Without this gate, `close` could flip
  during the forming bar and a setup could appear and later repaint away before the
  bar actually finished. Only added after the fact — an earlier version fired
  purely on `close` with no confirmation gate.
- Every zone is checked in the **same loop pass** on that bar; a zone that validates
  builds and pushes its own `ConfirmedSetup` **inline, immediately**, rather than
  recording a single "winner" to build after the loop. This distinction matters: a
  design that captured only one "winner" per bar via shared local variables let a
  later-processed zone in the same loop silently overwrite an earlier zone that also
  validated on that exact bar — losing one of two simultaneously-valid setups without
  any visible symptom. Confirmed and fixed via debug instrumentation (§10).
- Whether validated or not, `c1Set` is always cleared and the dotted line deleted at
  the end of this check; the "C1" label is deleted too **unless** validation
  succeeded (in which case it's *promoted* into the new `ConfirmedSetup`, see §4).

## 4. `ConfirmedSetup` — the fully validated, drawn setup

```
type ConfirmedSetup
    label c1Label, c2Label, c3Label
    box   zoneBox                 // the "C2 body zone" box (open↔close of C2)
    line  entryLine
    label entryLabel
    float entry                    // median(C2 open, C2 close)
    float sl2Level                  // C2close - 2 * unit  (see below)
    int   c2Bar
    int   srcFormBar                 // formBar of the FvgZone this came from
    array<line>  slLines, tpLines
    array<label> slLabels, tpLabels
```

On confirmation:
- `unit = c2Close - c2Open` (negative for a short's bearish C2).
- **Entry** = `median(open, close)` of C2, i.e. the exact 0.5 of C2's own body.
- **Reference scale**, all expressed as multiples of `unit`, `lvl = c2Close - n * unit`:
  - `0` = C2's close, `1` = C2's open (defines the C2 body box drawn on the chart).
  - Stop side (gray, `stopColor`): `n = 1.5` and `n = 2.0` ("SL 1.5", "SL 2").
  - Target side (green, `tpColor`): `n ∈ {-1, -2, -4, -6, -7}` ("TP 1"…"TP 7", drawn
    label uses `-n` so it reads as a positive "TP 1" etc.).
  - Because `unit` is negative for a short, the same formula auto-mirrors direction —
    there is no separate long/short branching for the level math itself.
- The zone's own pending `c1PendingLabel` is **promoted** into `newSetup.c1Label`
  (reused, not recreated) and repositioned to align with C2's own label height.
- The originating zone is marked `used := true; swept := false` at this point — this
  is what the reuse gate (§3.1) keys off of for that zone's future touches.
- The new `ConfirmedSetup` is pushed to `longSetups` / `shortSetups`
  (`array<ConfirmedSetup>`) — **not** a single global variable. See §5 for why.

### 4.1 Eviction (FIFO)

```
array.push(longSetups, newSetup)
if array.size(longSetups) > maxSetups
    oldest = array.get(longSetups, 0)
    f_deleteSetup(oldest)
    array.remove(longSetups, 0)
```

- `maxSetups` (input, default `1`) caps how many confirmed setups stay visible **per
  direction independently** — not a combined total across both directions.
- New setups are always appended at the tail; the oldest (index `0`) is evicted first
  once the cap is exceeded. This is a simple FIFO, not based on any scoring/quality
  metric.

## 5. Direction Filter

```
directionFilter = "Beide" | "Nur Bullish" | "Nur Bearish"
longEnabled  = directionFilter != "Nur Bearish"
shortEnabled = directionFilter != "Nur Bullish"
```

Disabling a direction is a **hard gate**, not a display filter:
- FVG registration for that direction never happens (`if bullFvg and longEnabled`),
  so that direction's zone array stays permanently empty — no tracking, no boxes, no
  C1/C2 processing at all for it.
- The entire LONG SETUP / SHORT SETUP section is wrapped in `if longEnabled` / `if
  shortEnabled`.
- `longEntryEvent` / `shortEntryEvent` are declared `= false` **outside** that `if`
  block (they must exist unconditionally for the `alertcondition()` calls at the
  bottom, which — being a Pine top-level construct — cannot themselves be made
  conditional). A disabled direction's event flag is therefore structurally always
  `false`, so its alert can never fire, without needing to touch the
  `alertcondition()` call itself.

There used to be an additional **"only one setup globally" rule** (a fresh
confirmation in either direction cleared whatever the other direction currently had
on the chart). It was removed entirely in favor of the independent per-direction
`longSetups`/`shortSetups` arrays plus this direction filter — long and short now run
fully independently, with no cross-clearing.

## 6. Invalidation

Two independent rules, each applied **per individual `ConfirmedSetup`** (not against
a single global setup), via the safe reverse-iteration-with-removal pattern (`while i
>= 0 ... array.remove(arr, i) ... i -= 1` — chosen over `for i = size-1 to 0`, which
has an off-by-one execution quirk in Pine when the array becomes empty mid-loop):

1. **C3-never-returned ("Rule 1")**: if the candle right after C2 (i.e. "C3") never
   traded back into the entry level, and a 4th candle since C2 has now formed, the
   setup is deleted:
   ```
   if bar_index == s.c2Bar + 2 and low[1] > s.entry   // long; short mirrors with high[1] < s.entry
   ```
2. **SL2 reached ("Rule 2")**: if price reaches the setup's own "SL 2" reference
   level, the setup is deleted:
   ```
   if bar_index > s.c2Bar and low <= s.sl2Level        // long; short mirrors with high >= s.sl2Level
   ```
   The `bar_index > s.c2Bar` guard is required: `sl2Level` is computed from the C2
   candle's own close, so that **same** candle's own wick can already exceed it (2×
   its own body's distance) — without this guard, a setup could be built and then
   deleted again within the very same bar it was confirmed, before ever being visible.
   Confirmed via debug instrumentation against a real case where this happened.

Neither rule references `longC1Zone`/`shortC1Zone`-style singular state; both iterate
the live array, so each of up to `maxSetups` visible setups per direction is checked
and can be invalidated independently of the others.

## 7. Mitigation

```
// bullish zone: if close < zM.bot -> remove
// bearish zone: if close > zM.top -> remove
```

- A zone is mitigated **only** once a candle's **body close** — not a wick —
  crosses fully beyond its far boundary. This is the deliberate, final definition:
  an earlier attempt switched this to wick-based mitigation and was explicitly
  reverted per direct correction ("wir gehen zurück auf die ursprüngliche Definition
  eines mitigierten FVGs" — "only when a candle's body closes outside a FVG,
  enclosing it, is it considered mitigated").
- **A fully mitigated FVG is invalid, no exceptions** — including for a zone that
  still has a pending C1 on it. An attempt was made to give a still-pending C1 a
  one-bar grace period past its own zone's mitigation; this was an *unauthorized*
  deviation from the rule above and was reverted. If a candle both touches a zone
  (registering C1) and closes beyond it (mitigating it) in the same bar, that C1 is
  lost — this is expected, not a bug.
- Mitigation runs **after** C1 registration in the per-bar execution order (§9), so a
  candle that qualifies as a fresh C1 still gets to register before its zone is
  possibly removed at the end of the same bar.
- Mitigated zones also clean up their own `boxRef`/`c1Line`/`c1PendingLabel` before
  being removed from the array — no orphaned drawings.

## 8. Reuse Gate (Swing-Point Sweep)

Once a zone has `used = true` (has produced at least one confirmed setup), a **new**
C1 on that same zone is gated until a swing point that formed *since* that use is
swept, per classic ICT liquidity-sweep logic:

```
swingHighConfirmed = high[1] > high[2] and high[1] > high[0]   // classic 3-bar fractal
swingLowConfirmed  = low[1]  < low[2]  and low[1]  < low[0]

for z in bearFvgs     // reuse gate for short-side zones needs a SWING HIGH swept
    if z.used and not z.swept
        if not na(z.lastSwing) and high > z.lastSwing and low <= z.top and high >= z.bot
            z.swept := true
        if not z.swept and swingHighConfirmed
            z.lastSwing := high[1]
// bullFvgs mirrors with swing LOWs
```

- The swing point tracked is the **most recent** 3-bar fractal high/low formed since
  the zone's last use — `lastSwing` is only set while `not z.swept`, so it locks onto
  the first qualifying swing after reuse eligibility starts.
- **Sweep is wick-only** (`high > z.lastSwing`, no close requirement) — matches
  standard ICT liquidity-sweep definition (a stop-run, not a breakout close).
- The sweeping candle's range must also be **inside the zone itself**
  (`low <= z.top and high >= z.bot`) — the sweep has to happen *while trading inside
  that same zone*, not anywhere on the chart.
- Once `swept = true`, the very next qualifying touch (§3.1) is allowed to register a
  fresh C1 on that zone again; confirming that new setup resets `swept := false` and
  the cycle repeats.
- This entire mechanism only runs for zones with `used = true` — a zone on its first
  ever use needs no swing sweep.

## 9. Per-Bar Execution Order

The script is not organized by "feature" independently — order matters, and several
bugs in this project's history were specifically ordering bugs. The actual sequence,
top to bottom, every bar:

1. **FVG detection** — register any new bull/bear FVG (gated by direction filter).
2. **Swing points** — update `lastSwing`/`swept` for zones with `used = true`.
3. **LONG SETUP**: C2 validation (over zones with a pending C1) → new C1 registration
   → C1 dotted-line extension.
4. **SHORT SETUP**: mirror of step 3.
5. **Invalidation** — Rule 1 (C3 never returned) then Rule 2 (SL2 reached), long then
   short, each as an independent per-setup array scan.
6. **Mitigation** — remove any zone whose body-close now fully invalidates it. Runs
   **after** steps 3–4 so a same-bar C1 registration is not pre-empted by this same
   bar's mitigation check on the same zone (§7).
7. **FVG box lifecycle** — decide, per zone, whether it still shows a box (§9.1).
8. **Label pinning** — if `extendInfinite`, re-pin Entry/SL/TP labels to the current
   bar every bar (their lines already auto-extend via `extend.right`).
9. **Debug table refresh** (if `debugValidation`) — live per-zone pending-C1 /
   reuse-gate summary.
10. **Alerts** — `alertcondition()` for `longEntryEvent` / `shortEntryEvent`.

### 9.1 FVG box visibility

```
keepBull = debugAllZones or i == lastIdxBull or f_formBarLive(z.formBar, longSetups)
```

A zone's box (and its `"FVG+/- <TF> MBZ"` text, §11) is shown only if:
- `debugAllZones` is on (shows every tracked zone, for diagnostics), **or**
- it's the **newest** zone in its array (`i == lastIdxBull`), **or**
- its `formBar` matches the `srcFormBar` of **any** currently-alive `ConfirmedSetup`
  in that direction's array (`f_formBarLive`) — i.e. it's backing a setup that's
  still on the chart.

A zone whose C1 is merely *pending* (no C2 yet) shows **no box** unless it happens to
also be the newest zone — the box only appears once C1 is "complete", not while it's
still an open question. Every other zone (old, resolved one way or another) is fully
decluttered: box + FVG text deleted.

`box.new()`'s left edge is clamped via `f_safeLeft(lb) = math.max(lb, bar_index -
9500)` — Pine rejects `xloc.bar_index` drawings whose bar is more than ~10,000 bars
behind the current one, and a zone can legitimately stay non-mitigated (and thus
eligible for a box) far longer than that, e.g. under `debugAllZones` or while backing
a long-lived confirmed setup. The clamp trades perfect historical left-edge accuracy
for never crashing; visually irrelevant since such an old edge would be off-screen
anyway.

## 10. Debug Tooling (opt-in, `"Anzeige"` input group)

Two toggles, purely diagnostic, no effect on trading logic:

- **`debugAllZones`** — every tracked zone (not just the newest/confirmed-backing one)
  shows its box, so you can visually identify which zone a given touch actually
  belongs to (a zone with only a pending C1 is otherwise invisible by design, §9.1).
- **`debugValidation`** — populates a fixed top-left `table` (`dbgTable`, 1×6) with:
  - Row 0/1: live summary per direction of every zone with a pending C1
    (`PENDING c1Bar=... top/bot`) and every zone currently reuse-gated
    (`GATED(used,unswept) top/bot lastSwing=...`) — refreshed every bar.
  - Row 2/3: the most recent C2 validation attempt per direction, with the actual
    `close`/`open`/`c1Open`/`c1Close`/`median` values and the computed `validated`
    boolean.
  - Row 4/5: the most recent new-C1 registration per direction, with which zone
    (`formBar`/`top`/`bot`) it attached to.

This exists because several reported "missing C1/C2" issues turned out to require
seeing the script's actual computed values rather than inferring them from candle
pixel positions on a screenshot — a table (fixed position, overwritten each event) was
adopted after an earlier attempt using scattered `label.new()` calls proved unreadable
once multiple events landed close together on the chart.

## 11. Visual Output Reference

| Element | Source | Notes |
|---|---|---|
| FVG box | `FvgZone.boxRef` | Only shown per §9.1. Teal (`bullPoiColor`) / red (`bearPoiColor`), no border. |
| FVG text | box's native `text` | `"FVG+ <TF> MBZ"` / `"FVG- <TF> MBZ"`, `<TF>` = `timeframe.period` (the chart's own TF). Uses `box.new`'s built-in `text_halign`/`text_valign`/`text_wrap` — clipped to the box bounds natively, not a separately positioned label (an earlier separate-label approach overflowed past the box edge). |
| "C1" pending label | `FvgZone.c1PendingLabel` | Plain text (`label.style_none`), positioned below the zone (long) / above it (short), offset by `tagOffset`. |
| C1 dotted line | `FvgZone.c1Line` | 0.5-of-C1-body threshold, extends right each bar while pending. |
| "C1"/"C2"/"C3" labels | `ConfirmedSetup.c1Label/c2Label/c3Label` | All three at the same height (below for long, above for short). C1 is the *promoted* pending label; C2/C3 are new. |
| C2 body zone box | `ConfirmedSetup.zoneBox` | Spans C2's open↔close, one bar wide. |
| Entry line/label | `ConfirmedSetup.entryLine/entryLabel` | Style/width/text-alignment all configurable (`lvlLineStyleInput`, `lvlLineWidth`, `lvlTextH`, `lvlTextV`). |
| SL 1.5 / SL 2 lines | `ConfirmedSetup.slLines/slLabels` | Gray (`stopColor`). |
| TP 1/2/4/6/7 lines | `ConfirmedSetup.tpLines/tpLabels` | Green (`tpColor`). |
| Debug table | `dbgTable` | Only when `debugValidation` is on. |

All C1/C2/C3/Entry/SL/TP labels use `label.style_none` (plain text, no
box/pointer) — an explicit choice over TradingView's default label-with-pointer
style.

## 12. Full Input Reference

**Group "FVG / POI"**
| Input | Default | Purpose |
|---|---|---|
| `showFvgBox` | `true` | Show/hide FVG zone boxes entirely. |
| `bullPoiColor` / `bearPoiColor` | teal/red, 85% transparent | FVG box fill. |
| `showFvgLabel` | `true` | Show/hide the `"FVG+/- <TF> MBZ"` box text. |
| `bullFvgTextColor` / `bearFvgTextColor` | teal / red (opaque) | FVG text color (separate from the transparent box fill color). |
| `fvgTextH` | `"Links"` | `Links`/`Mitte`/`Rechts` → box `text_halign`. |
| `fvgTextV` | `"Oben"` | `Oben`/`Mitte`/`Unten` → box `text_valign`. |

**Group "MBZ / Entry"**
| Input | Default | Purpose |
|---|---|---|
| `showC1Line` | `true` | Show/hide the dotted C1 0.5-threshold line. |
| `showZoneBox` | `true` | Show/hide the C2 body-zone box. |
| `showEntry` | `true` | Show/hide the entry line/label. |
| `showStopLvls` | `true` | Show/hide SL 1.5/2. |
| `showTpLvls` | `true` | Show/hide TP 1-7. |
| `extendInfinite` | `true` | Extend level lines to infinity (chasing the current bar) vs. a fixed `projectionBars` length. |
| `projectionBars` | `40` | Used only when `extendInfinite = false`. |
| `lvlLineStyleInput` | `"Gestrichelt"` | `Durchgezogen`/`Gestrichelt`/`Gepunktet` — Entry/SL/TP line style. |
| `lvlLineWidth` | `1` | Entry/SL/TP line width (1-4). |
| `lvlTextH` | `"Links"` | Entry/SL/TP label horizontal text alignment. |
| `lvlTextV` | `"Auf der Linie"` | `Über der Linie`/`Auf der Linie`/`Unter der Linie` — vertical offset via `tagOffset`. |
| `longColor` / `shortColor` | lime / red | Primary direction colors (C1/C2/C3, entry line, box). |
| `stopColor` / `tpColor` | gray / green | SL / TP line+label color. |
| `directionFilter` | `"Beide"` | `Beide`/`Nur Bullish`/`Nur Bearish` — hard-gates a whole direction, §5. |
| `maxSetups` | `1` | Confirmed setups kept visible per direction (independent per direction, FIFO eviction), §4.1. |

**Group "Anzeige"**
| Input | Default | Purpose |
|---|---|---|
| `showLabels` | `true` | Master toggle for C1/C2/C3/Entry/SL/TP **labels** (lines/boxes unaffected). |
| `debugAllZones` | `false` | Show every tracked FVG zone's box, §10. |
| `debugValidation` | `false` | Populate the debug table, §10. |
| `tagOffsetMult` | `1.0` | Multiplier on `ta.atr(14)` for label offset distance from candles — scale-independent across instruments/timeframes. |

## 13. Known Constraints / Non-Goals

- **Single timeframe only.** FVG detection, C1/C2, everything — all evaluated purely
  on the chart's own timeframe. No multi-timeframe confluence.
- **No backtest/strategy logic.** This is an `indicator`, not a `strategy` — no
  simulated fills, equity curve, or performance stats. Entry/SL/TP are visual
  reference levels only.
- **Pine's `xloc.bar_index` ~10,000-bar limit** applies to FVG box left edges (§9.1);
  mitigated via clamping, not via switching to `xloc.bar_time`.
- **No repainting protection beyond `barstate.isconfirmed`** on the C2 validation
  step specifically — other elements (e.g. C1 registration itself, mitigation) run
  on live/intrabar values on the currently-forming bar, same as they always have; only
  the C2→entry/SL/TP confirmation step was hardened against intrabar flicker.
- **No alert-message customization** beyond the fixed `"MBZ Long/Short Entry bei
  {{close}}"` templates.

## 14. Change Log Summary (architectural milestones)

For full detail, see the git history on `pine/MBZ_Entry_Model.pine`. Major
architectural turning points, in order:

1. Scalar (single-instance) C1/C2 tracking per direction → replaced with per-zone
   independent tracking (`FvgZone.c1Set` etc.) once real market data proved the
   scalar "newest wins" model silently dropped valid setups.
2. Single global "one confirmed setup on the whole chart" → replaced with
   `array<ConfirmedSetup>` per direction, a direction filter, and `maxSetups` with
   FIFO eviction.
3. Single "winner" locals per validation-bar → validating zones now build/push their
   own `ConfirmedSetup` inline, so two zones validating on the same bar both survive.
4. Mitigation wick-based experiment → reverted to body-close-only, permanently.
5. A same-bar-mitigation exception for pending C1s → implemented, found to violate
   the mitigation rule, reverted.
6. `barstate.isconfirmed` gate added to C2 validation to stop intrabar flicker.
7. FVG label as a separately positioned `label.new()` → replaced with the box's own
   native `text`/`text_halign`/`text_valign` to eliminate manual overflow handling.
