---
name: VeeTee Abyss
colors:
  background: '#07111f'
  on-background: '#f8fafc'
  surface: '#0d1a2b'
  surface-container-low: '#101f32'
  surface-container: '#14263c'
  surface-bright: '#0d1a2b'
  on-surface: '#f8fafc'
  on-surface-variant: '#94a3b8'
  outline: '#64748b'
  outline-variant: '#334155'
  primary: '#14b8a6'
  on-primary: '#07111f'
  primary-container: '#0f9f91'
  on-primary-container: '#f8fafc'
  secondary: '#22d3ee'
  on-secondary: '#07111f'
  tertiary: '#818cf8'
  on-tertiary: '#f8fafc'
  error: '#fb7185'
  on-error: '#f8fafc'
  warning: '#fbbf24'
  success: '#34d399'
typography:
  display-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '700'
    lineHeight: 28px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 17px
    fontWeight: '700'
    lineHeight: 20px
    letterSpacing: -0.02em
  title-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: '0'
  body-base:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: '0'
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: '0'
  caption-mono:
    fontFamily: SFMono-Regular
    fontSize: 10px
    fontWeight: '400'
    lineHeight: 14px
    letterSpacing: '0'
rounded:
  sm: 7px
  DEFAULT: 12px
  md: 15px
  lg: 18px
  full: 9999px
spacing:
  unit: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  gutter: 12px
  margin-mobile: 14px
  margin-desktop: 24px
---

# Design System: VeeTee Abyss
**Project ID:** 14203795258412928706

## 1. Visual Theme & Atmosphere

VeeTee Abyss is a deep-sea mission-control aesthetic for a realtime Vietnamese voice-AI server dashboard. The mood is nocturnal, technical, and calm under pressure: near-black abyssal blues lit by bioluminescent teal, like monitoring a submarine that talks. Generous glass surfaces float over faint aurora gradients, keeping dense protocol telemetry readable without anxiety.

Whitespace is breathable but information-dense: a 1240px console grid holds status cards, a live chat column, and stacked test panels. Motion is restrained — 160ms ease transitions, pulsing status dots, ambient voice glow — and fully disabled under prefers-reduced-motion.

## 2. Color Palette & Roles

### Primary Foundation
- **Abyss Background** `#07111f` — app canvas with teal/indigo aurora radial glows.
- **Panel Surface** `#0d1a2b`, **Raised Surface** `#101f32`, **Sunken Surface** `#14263c` — three-step glass elevation with hairline slate borders.

### Accent & Interactive
- **Signal Teal** `#14b8a6` (strong `#0f9f91`, hover `#12ad9d`) — primary CTAs, brand mark, live voice glow.
- **Sonar Cyan** `#22d3ee` — links, focus rings, secondary actions (`#0e7490` button fill).
- **Nebula Indigo** `#818cf8` — tertiary accents, badges.

### Typography & Text Hierarchy
- **Foam White** `#f8fafc` primary text; **Sonar Muted** `#94a3b8` secondary; **Depth Muted** `#64748b` captions.

### Functional States
- **Go Green** `#34d399`, **Alert Amber** `#fbbf24`/`#fcd34d`, **Hull Rose** `#fb7185` (danger fill `rgba(190,24,93,.88)`), each with soft halo rings on status dots.

## 3. Typography Rules

### Hierarchy & Weights
Inter everywhere (system-ui fallback); SFMono-Regular/Consolas for code, badges, and telemetry values. Titles tight-tracked (-0.02em), body relaxed. Vietnamese diacritics must stay legible — no condensed display faces.

### Spacing Principles
Body 13px/20px; panel titles 14px semibold with 10.5px muted subtitles; micro-labels 9–11px. Chat bubbles capped at 88% width on mobile.

## 4. Component Stylings

### Buttons
Full-width action buttons (min-height 45px mobile) with 12px radius; primary teal, cyan secondary, rose danger. Disabled states dim without layout shift; 160ms ease on transform/background/border.

### Cards & Panels
Panels at 18px radius (15px mobile) with layered navy gradients and 18px/45px ambient shadow. Status cards show dot + label + mono value. Header is sticky frosted glass (blur 18px).

### Navigation
Single sticky header: brand mark (38px rounded tile, teal gradient, "VT" wordmark) left, scrolling mono stack badges right (hidden under 390px).

### Inputs & Forms
Dark sunken inputs (`#0d1a2b`, 12px radius, slate borders); cyan 3px focus ring with offset. Textareas min 125px mobile at 16px to prevent iOS zoom. Selects share the input style. Labels are 11px muted helpers above fields.

### Domain-Specific Components
- **Chat conversation panel** with message bubbles, sticky composer, mic toggle, and live TTS playback state.
- **Voice pipeline stage tracker** with per-stage timing values in mono.
- **Protocol test console** with endpoint list, raw JSON view, and diagnostics grid.
- **Voice & Model panel**: two labeled selects (reply voice, LLM model) with save buttons and a status line confirming what is active.

## 5. Layout Principles

### Grid & Structure
Max 1240px centered console; 3-column status grid (2 on small, 1 spanning connection card); workspace grid with chat column + side stack of panels.

### Whitespace Strategy
4px base unit; 24px desktop page padding, 14px mobile with safe-area insets; 12px panel gaps (10px mobile).

### Alignment & Visual Balance
Left-aligned body copy, centered welcome states; chat composer sticks to panel bottom; helper text sits directly under its control.

### Responsive Behavior & Touch
Breakpoints 960px (single column), 680px (compact mobile), 390px (minimal). Touch targets ≥45px; full-width buttons on mobile; `100dvh` conversation height; reduced-motion support.

## 6. Design System Notes for Stitch Generation

### Language to Use
"Dark abyssal ops console, bioluminescent teal on deep navy glass, calm Vietnamese voice-AI mission control."

### Color References
Abyss `#07111f`, Signal Teal `#14b8a6`, Sonar Cyan `#22d3ee`, Foam `#f8fafc`, Muted `#94a3b8`, Go `#34d399`, Amber `#fbbf24`, Rose `#fb7185`.

### Component Prompts
- "Glass status card grid with glowing status dots and mono telemetry values on deep navy."
- "Sticky frosted-glass header with teal brand tile and scrolling mono stack badges."
- "Dark chat panel with message bubbles, sticky composer with mic and send buttons."

### Incremental Iteration
Keep new screens on the same glass language: 18px panels, teal primary actions, mono micro-labels, Vietnamese-first copy. Polish the Voice & Model panel first — clearer active-state feedback and less prompt-loop friction on first load.
