'use client'

// Adapted from the "Axiom" formula hero: the same white graph-paper page, serif headline with a drifting wave under its
// accent word, floating doodles, and one card rising from the bottom edge with a stepper. Where the maths sat, each
// stage of the scraper shows its own copy. Before a run the card steps through an EXAMPLE run (labelled as one); during
// a real run every line is what the server actually reported, streamed as each stage finishes.

import Link from 'next/link'
import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type SVGProps } from 'react'
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from 'motion/react'
import { ArrowRightIcon, ChevronLeftIcon, ChevronRightIcon, LoaderCircleIcon, MenuIcon, XIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { DECISIONS, ROWS, outcome, valueText } from '@/lib/format'

type Tone = 'ok' | 'flag' | 'fail' | 'skip' | 'wait' | 'idle'
type StageId = 'guards' | 'fetch' | 'clean' | 'extract' | 'evidence'
type Step = { id: string; label: string; state: 'ok' | 'flag' | 'skip' | 'fail'; detail: string }
type Line = { k: string; v: string; quote?: string; tone?: Tone }
type Fact = { label: string; value: string }
type Stage = { id: StageId; tab: string; question: string; lines: Line[]; facts: Fact[]; insight: string; state: Tone }
type Result = {
  ok: true; domain: string; decision: string; defence: string; saved: boolean; from_cache: boolean
  failure: string | null; explanation: string | null; flags: string[]; steps: Step[]
  rows: { row: string; status: string; outcome: string; field: string | null; value: unknown; source_url: string | null; quote: string | null }[]
  fields: { field: string; value: unknown; quote: string | null; source_url: string | null }[]
  fetch: { status: number; bytes: number; redirects: number; truncated: boolean; error: string | null } | null
  cleaned: { words: number; tokens_before: number; tokens_after: number; preview: string; footer: string } | null
  extract_note: string | null
  model: { calls: number; prompt_tokens: number; output_tokens: number; key_configured: boolean }
  guards: { visitor_runs_used: number; visitor_runs_limit: number; budget_used: number; budget_limit: number | null; hunter_used: number; hunter_cap: number }
  elapsed_seconds: number; record_path: string
}
export type ScraperConfig = {
  disabled: boolean; modelReady: boolean; perVisitor: number; global: number; budget: number | null; used: number | null
  model: string; delay: number
}

const STAGES: { id: StageId; tab: string; question: string; insight: string }[] = [
  { id: 'guards', tab: 'Guards', question: 'Is this run allowed to start?', insight: 'Limits are checked before any request leaves the server, so a crawler cannot spend the budget.' },
  { id: 'fetch', tab: 'Fetch', question: 'What does the site let a bot read?', insight: 'robots.txt is read first. A block, a CAPTCHA or a login stops the run: it never tries to get past one.' },
  { id: 'clean', tab: 'Clean', question: 'What is left once the noise is gone?', insight: 'Navigation, footers, cookie banners and scripts are stripped. A page under 200 words is flagged thin, never guessed at.' },
  { id: 'extract', tab: 'Extract', question: 'What can the page actually prove?', insight: 'Every value must quote the page. A quote that is not on the page is thrown away in code, not trusted.' },
  { id: 'evidence', tab: 'Evidence', question: 'Does it fit the ICP, and why?', insight: 'The hard rules are applied in code. The soft rows only rank, and never qualify a company on their own.' },
]
const STAGE_OF: Record<string, StageId> = { guards: 'guards', cache: 'guards', robots: 'fetch', fetch: 'fetch', clean: 'clean', extract: 'extract', evidence: 'evidence', save: 'evidence' }
const ROW_LABEL = ROWS as Record<string, { label: string }>
const DECISION = DECISIONS as Record<string, { label: string }>

// ---- the example run shown before anyone runs anything (clearly labelled as an example)
const EXAMPLE: Record<StageId, { lines: Line[]; facts: Fact[] }> = {
  guards: {
    lines: [
      { k: 'sign-in', v: 'none needed, this is an open demo', tone: 'ok' },
      { k: 'your runs', v: 'run 1 of 3 this hour', tone: 'ok' },
      { k: 'whole demo', v: '4 of 20 runs this hour', tone: 'ok' },
      { k: 'cache', v: 'no fresh record, so this is a real run', tone: 'ok' },
    ],
    facts: [{ label: 'Your runs', value: '1 of 3' }, { label: 'Model calls today', value: '3 of 20' }, { label: 'Private addresses', value: 'refused' }],
  },
  fetch: {
    lines: [
      { k: 'robots.txt', v: 'GET acme.com/robots.txt → 200, the page is allowed', tone: 'ok' },
      { k: 'pause', v: 'waits 2 s, a polite delay', tone: 'ok' },
      { k: 'homepage', v: 'GET acme.com/ → 200, 6,253 bytes', tone: 'ok' },
      { k: 'links', v: 'none followed: one page, plain fetch, no browser', tone: 'skip' },
    ],
    facts: [{ label: 'Status', value: '200' }, { label: 'Bytes', value: '6,253' }, { label: 'Redirects', value: '0' }, { label: 'Pages', value: '1' }],
  },
  clean: {
    lines: [
      { k: 'kept', v: 'Acme Ledger: automated revenue reconciliation for finance teams' },
      { k: 'kept', v: 'Acme Ledger is a cloud platform that helps B2B finance and accounting teams reconcile revenue…' },
      { k: 'kept', v: 'We recently raised our Series B to expand our engineering and customer success teams…' },
      { k: 'removed', v: 'navigation, cookie banner, footer, scripts', tone: 'skip' },
    ],
    facts: [{ label: 'Words kept', value: '255' }, { label: 'Tokens', value: '366 → 331' }, { label: 'Text aimed at an AI', value: '0 blocks' }],
  },
  extract: {
    lines: [
      { k: 'buyer_type', v: 'business', quote: 'finance teams', tone: 'ok' },
      { k: 'funding_stage', v: 'Series B', quote: 'Series B', tone: 'ok' },
      { k: 'certifications', v: 'null, none stated on the page', tone: 'skip' },
      { k: 'check', v: 'each quote is looked up in the page text, in code', tone: 'ok' },
    ],
    facts: [{ label: 'Model calls', value: '1' }, { label: 'Tokens in / out', value: '120 / 30' }, { label: 'Values kept', value: '4 of 8' }],
  },
  evidence: {
    lines: [
      { k: 'Already certified?', v: 'Not evaluated: only the homepage was read', tone: 'skip' },
      { k: 'B2B SaaS?', v: 'Resolved: business', tone: 'ok' },
      { k: 'Right stage?', v: 'Resolved: funding stage', tone: 'ok' },
      { k: 'Compliance pressure?', v: 'Resolved: enterprise customers', tone: 'ok' },
      { k: 'decision', v: 'Partial run. The command-line agent reads up to 4 more pages, then contacts and drafts.' },
    ],
    facts: [{ label: 'Rows resolved', value: '3 of 4' }, { label: 'Decision', value: 'Partial run' }, { label: 'Flags', value: '2' }],
  },
}

const clip = (s: string, n = 150) => (s.length > n ? `${s.slice(0, n)}…` : s)
const num = (n: number) => n.toLocaleString('en-US')

function exampleStages(): Stage[] {
  return STAGES.map(s => ({ ...s, ...EXAMPLE[s.id], state: 'idle' as Tone }))
}

// Turns the streamed steps (and, once it arrives, the final result) into the five stages' copy.
function liveStages(steps: Step[], r: Result | null, running: boolean): Stage[] {
  let waited = false

  return STAGES.map(def => {
    const mine = steps.filter(s => STAGE_OF[s.id] === def.id)
    const lines: Line[] = mine.filter(s => s.id !== 'save').map(s => ({ k: s.label.toLowerCase(), v: s.detail, tone: s.state }))
    const facts: Fact[] = []
    let insight = def.insight

    const tones = mine.map(s => s.state)
    let state: Tone = tones.includes('fail') ? 'fail' : tones.includes('flag') ? 'flag' : tones.length && tones.every(t => t === 'skip') ? 'skip' : tones.length ? 'ok' : 'idle'

    if (running && !mine.length && !waited) { state = 'wait'; waited = true }

    // A cache hit makes no request, so the stages that normally fetch and clean say that plainly instead of looking empty.
    if (r?.from_cache && (def.id === 'fetch' || def.id === 'clean') && !mine.length) {
      lines.push({ k: 'cache', v: def.id === 'fetch' ? 'Nothing was fetched: this domain was run recently, so the stored record was reused.' : 'Nothing was cleaned this time. The cleaned text is not stored, only the extracted fields and the evidence.', tone: 'skip' })
      state = 'skip'
    }

    if (def.id === 'guards' && r) {
      facts.push({ label: 'Your runs', value: `${r.guards.visitor_runs_used} of ${r.guards.visitor_runs_limit}` })
      facts.push({ label: 'Model calls today', value: r.guards.budget_limit == null ? 'no budget set' : `${r.guards.budget_used} of ${r.guards.budget_limit}` })
      facts.push({ label: 'Hunter', value: `unused (${r.guards.hunter_used}/${r.guards.hunter_cap})` })
    }
    if (def.id === 'fetch' && r) {
      if (r.fetch && !r.fetch.error) facts.push({ label: 'Status', value: String(r.fetch.status) }, { label: 'Bytes', value: num(r.fetch.bytes) }, { label: 'Redirects', value: String(r.fetch.redirects) }, { label: 'Pages', value: '1' })
      if (r.failure && r.explanation) { lines.push({ k: 'stopped', v: r.explanation, tone: 'fail' }); insight = 'A flag is a correct outcome. The agent stopped at the barrier, recorded why and queued the company for a human.' }
    }
    if (def.id === 'clean' && r?.cleaned) {
      r.cleaned.preview.split('\n').filter(Boolean).slice(0, 5).forEach(l => lines.push({ k: 'kept', v: clip(l) }))
      const inj = r.flags.find(f => f.startsWith('prompt_injection_ignored:'))
      facts.push({ label: 'Words kept', value: num(r.cleaned.words) }, { label: 'Tokens', value: `${num(r.cleaned.tokens_before)} → ${num(r.cleaned.tokens_after)}` }, { label: 'Text aimed at an AI', value: inj ? `${inj.split(':')[1]} blocks, ignored` : '0 blocks' })
      if (state === 'flag') insight = mine.find(s => s.state === 'flag')?.detail ?? insight
    }
    if (def.id === 'extract' && r) {
      const kept = r.fields.filter(f => f.value !== null && f.value !== undefined)
      kept.forEach(f => lines.push({ k: f.field, v: valueText(f.value) ?? '', quote: f.quote ?? undefined, tone: 'ok' }))
      if (r.fields.length) lines.push({ k: 'others', v: `${r.fields.length - kept.length} fields null: the page did not state them`, tone: 'skip' })
      if (r.model.calls) facts.push({ label: 'Model calls', value: String(r.model.calls) }, { label: 'Tokens in / out', value: `${num(r.model.prompt_tokens)} / ${num(r.model.output_tokens)}` }, { label: 'Values kept', value: `${kept.length} of ${r.fields.length}` })
      if (state === 'skip') insight = r.extract_note ?? insight
    }
    if (def.id === 'evidence' && r) {
      r.rows.forEach(x => lines.push({ k: ROW_LABEL[x.row]?.label ?? x.row, v: `${outcome(x.outcome).label}${x.field ? `: ${valueText(x.value) ?? ''}` : ''}`, quote: x.quote ?? undefined, tone: x.status === 'resolved' ? 'ok' : 'skip' }))
      lines.push({ k: 'decision', v: `${DECISION[r.decision]?.label ?? r.decision}. ${r.defence}` })
      const resolved = r.rows.filter(x => x.status === 'resolved').length
      if (r.rows.length) facts.push({ label: 'Rows resolved', value: `${resolved} of 4` })
      facts.push({ label: 'Decision', value: DECISION[r.decision]?.label ?? r.decision }, { label: 'Flags', value: String(r.flags.length) })
      insight = r.saved ? 'Saved as a labelled partial record. Open it for the full evidence trail.' : 'Not saved: this outcome should be retried or is never stored.'
    }
    return { ...def, lines, facts, insight, state }
  })
}

// ---- doodles: small scraping sketches in the margins (faint, drifting)
const Page = (p: SVGProps<SVGSVGElement>) => (
  <svg viewBox='0 0 92 76' fill='none' aria-hidden {...p}>
    <rect x='6' y='8' width='80' height='60' rx='6' stroke='currentColor' strokeWidth='1.3' />
    <path d='M6 22H86' stroke='currentColor' strokeWidth='1' />
    <circle cx='14' cy='15' r='2' fill='currentColor' /><circle cx='21' cy='15' r='2' fill='currentColor' />
    <path d='M16 34h50M16 44h38M16 54h44' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round' />
  </svg>
)
const Funnel = (p: SVGProps<SVGSVGElement>) => (
  <svg viewBox='0 0 92 76' fill='none' aria-hidden {...p}>
    <path d='M8 10h76L54 40v26l-16-8V40Z' stroke='currentColor' strokeWidth='1.3' strokeLinejoin='round' />
    <path d='M22 22h48' stroke='currentColor' strokeWidth='1' strokeDasharray='2 4' />
  </svg>
)
const Shield = (p: SVGProps<SVGSVGElement>) => (
  <svg viewBox='0 0 84 84' fill='none' aria-hidden {...p}>
    <path d='M42 8 70 18v22c0 18-12 30-28 36C26 70 14 58 14 40V18Z' stroke='currentColor' strokeWidth='1.3' strokeLinejoin='round' />
    <path d='m29 42 9 9 18-20' stroke='currentColor' strokeWidth='1.6' strokeLinecap='round' strokeLinejoin='round' />
  </svg>
)
const Quote = (p: SVGProps<SVGSVGElement>) => (
  <svg viewBox='0 0 92 70' fill='none' aria-hidden {...p}>
    <path d='M10 38c0-12 6-20 16-22M40 38c0-12 6-20 16-22' stroke='currentColor' strokeWidth='1.6' strokeLinecap='round' />
    <circle cx='12' cy='42' r='6' fill='currentColor' /><circle cx='42' cy='42' r='6' fill='currentColor' />
    <path d='M8 62h74' stroke='currentColor' strokeWidth='2.4' strokeLinecap='round' opacity='.5' />
  </svg>
)
const SKETCH = { page: Page, funnel: Funnel, shield: Shield, quote: Quote }
const DOODLES: { kind: keyof typeof SKETCH | 'text'; text?: string; x: number; y: number; size?: number; tilt: number }[] = [
  { kind: 'page', x: 9, y: 14, size: 84, tilt: -6 },
  { kind: 'text', text: 'GET / → 200', x: 17, y: 52, tilt: 3 },
  { kind: 'funnel', x: 6, y: 80, size: 74, tilt: 4 },
  { kind: 'text', text: 'User-agent: *', x: 21, y: 94, tilt: -3 },
  { kind: 'shield', x: 90, y: 12, size: 70, tilt: 0 },
  { kind: 'text', text: '“Series B”', x: 84, y: 48, tilt: -3 },
  { kind: 'quote', x: 93, y: 80, size: 76, tilt: 5 },
  { kind: 'text', text: 'robots.txt ✓', x: 79, y: 94, tilt: 2 },
]

const WaveLine = (p: SVGProps<SVGSVGElement>) => (
  <svg viewBox='0 0 200 20' preserveAspectRatio='none' fill='none' aria-hidden {...p}>
    <path d='M0 10 Q12.5 1 25 10 T50 10 T75 10 T100 10 T125 10 T150 10 T175 10 T200 10' stroke='currentColor' strokeWidth='2.2' strokeLinecap='round' vectorEffect='non-scaling-stroke' />
  </svg>
)
const Mark = (p: SVGProps<SVGSVGElement>) => (
  <svg viewBox='0 0 22 22' aria-hidden {...p}>
    <rect x='1' y='3' width='20' height='4' rx='2' fill='var(--blue-dark)' />
    <rect x='4' y='9' width='14' height='4' rx='2' fill='var(--blue-dark)' />
    <rect x='7.5' y='15' width='7' height='4' rx='2' fill='var(--blue-light)' />
  </svg>
)

const NAV = [{ label: 'Review queue', href: '/' }, { label: 'Run a domain', href: '/run' }, { label: 'How it works', href: '/how' }]

function Navbar() {
  const [open, setOpen] = useState(false)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); button.current?.focus() } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <header className='relative z-30'>
      <nav aria-label='Main' className='axm-inset flex h-[76px] items-center justify-between gap-6'>
        <Link href='/' className='flex items-center gap-2.5'>
          <Mark className='size-7' />
          <span translate='no' style={{ fontFamily: 'var(--axm-display)', fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>ICP Qualifier</span>
        </Link>
        <ul className='absolute left-1/2 hidden -translate-x-1/2 items-center gap-1 lg:flex'>
          {NAV.map(l => <li key={l.href}><Link href={l.href} className='axm-navlink' aria-current={l.href === '/run' ? 'page' : undefined}>{l.label}</Link></li>)}
        </ul>
        <button ref={button} type='button' onClick={() => setOpen(v => !v)} aria-expanded={open} aria-controls='axm-menu' aria-label={open ? 'Close menu' : 'Open menu'}
          className='grid size-11 place-items-center rounded-full border lg:hidden' style={{ borderColor: 'var(--control)', background: 'transparent', cursor: 'pointer' }}>
          {open ? <XIcon className='size-5' aria-hidden /> : <MenuIcon className='size-5' aria-hidden />}
        </button>
      </nav>
      <AnimatePresence>
        {open && (
          <motion.ul id='axm-menu' initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}
            className='axm-menu absolute inset-x-3 top-full grid gap-1 rounded-2xl p-2 lg:hidden'>
            {NAV.map(l => <li key={l.href}><Link href={l.href} onClick={() => setOpen(false)} className='axm-navlink' aria-current={l.href === '/run' ? 'page' : undefined} style={{ height: 48 }}>{l.label}</Link></li>)}
          </motion.ul>
        )}
      </AnimatePresence>
    </header>
  )
}

const EASE = [0.22, 1, 0.36, 1] as const
const at = (s: number) => ({ '--axm-delay': `${s}s` }) as CSSProperties

type Phase = 'idle' | 'running' | 'done' | 'error'

export default function ScraperHero({ config }: { config: ScraperConfig }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [steps, setSteps] = useState<Step[]>([])
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState(0)
  const [auto, setAuto] = useState(true)
  const [held, setHeld] = useState(false)
  const [shownDomain, setShownDomain] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const reduce = useReducedMotion() ?? false

  const example = phase === 'idle' || phase === 'error'
  const stages = example ? exampleStages() : liveStages(steps, result, phase === 'running')
  const stage = stages[active]

  // Until someone runs something, the card walks through the example stages on its own; it waits while hovered.
  useEffect(() => {
    if (!auto || held || reduce || !example) return
    const id = setInterval(() => setActive(i => (i + 1) % STAGES.length), 4800)
    return () => clearInterval(id)
  }, [auto, held, reduce, example])

  const pick = (i: number) => { setAuto(false); setActive((i + STAGES.length) % STAGES.length) }

  async function run(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const domain = String(new FormData(e.currentTarget).get('domain') ?? '').trim()
    if (!domain) { setError('Enter a company domain, for example acme.com.'); setPhase('error'); input.current?.focus(); return }
    setAuto(false); setError(null); setResult(null); setSteps([]); setActive(0); setShownDomain(domain); setPhase('running')

    let res: Response
    try {
      res = await fetch('/api/run', { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/x-ndjson' }, body: JSON.stringify({ domain }) })
    } catch {
      setError('The request did not reach the server. Check your connection and try again.'); setPhase('error'); return
    }
    if (!res.body || !(res.headers.get('content-type') ?? '').includes('ndjson')) {
      const j = await res.json().catch(() => null)
      setError(j?.error?.message ?? 'The run failed. Try again in a minute.'); setPhase('error'); input.current?.focus(); return
    }
    const reader = res.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    let finished = false
    const handle = (line: string) => {
      const m = JSON.parse(line)
      if (m.type === 'step') {
        setSteps(prev => [...prev, m.step])
        const idx = STAGES.findIndex(s => s.id === STAGE_OF[m.step.id])
        if (idx >= 0) setActive(idx)
      } else if (m.type === 'done') {
        finished = true
        if (m.status !== 200 || !m.body.ok) { setError(m.body?.error?.message ?? 'The run failed.'); setPhase('error'); input.current?.focus(); return }
        setResult(m.body); setSteps(m.body.steps); setPhase('done'); setActive(4)
      }
    }
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let nl
      while ((nl = buf.indexOf('\n')) >= 0) { const l = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1); if (l) handle(l) }
    }
    if (!finished) { setError('The connection closed before the run finished. Try again.'); setPhase('error') }
  }

  const dec = result ? (DECISION[result.decision] ? result.decision : 'partial') : null
  const busy = phase === 'running'

  return (
    <MotionConfig reducedMotion='user'>
      <div className='axm-root antialiased'>
        <div className='axm-panel'>
          <div className='axm-backdrop' aria-hidden />
          <Navbar />

          <section aria-labelledby='axm-title' className='axm-layout'>
            <div className='axm-head axm-inset'>
              <div className='axm-doodles' aria-hidden>
                {DOODLES.map((d, i) => {
                  const place = { left: `${d.x}%`, top: `${d.y}%`, rotate: `${d.tilt}deg`, '--axm-float-delay': `${i * -1.3}s`, '--axm-in-delay': `${0.8 + i * 0.06}s` } as CSSProperties
                  if (d.kind === 'text') return <span key={i} className='axm-doodle axm-doodle-text' style={place}>{d.text}</span>
                  const Sketch = SKETCH[d.kind]
                  return <Sketch key={i} className='axm-doodle' style={{ ...place, width: d.size }} />
                })}
              </div>

              <h1 id='axm-title' className='axm-title axm-rise' style={at(0)}>
                Every claim, with its{' '}
                <span className='axm-accent'>
                  source.
                  <span className='axm-accent-line' aria-hidden><span className='axm-drift'><WaveLine className='block size-full' /></span></span>
                </span>
              </h1>
              <p className='axm-sub axm-rise' style={at(0.1)}>
                Give it one company domain and watch the scraper work, stage by stage: what the site allows, what is left once the noise is gone, what the page can prove, and whether it fits.
              </p>

              <form className='axm-form axm-rise' style={at(0.2)} onSubmit={run} noValidate>
                <label htmlFor='domain' className='sr-only'>Company domain</label>
                <input id='domain' ref={input} name='domain' className='axm-input' type='text' inputMode='url' autoComplete='off' autoCapitalize='none' spellCheck={false}
                  placeholder='acme.com…' disabled={config.disabled || busy} aria-describedby='axm-note' aria-invalid={phase === 'error' && !!error} />
                <button type='submit' className='axm-go' disabled={config.disabled || busy}>
                  {busy ? 'Running…' : 'Run scraper'}
                  <span className='axm-orb'>{busy ? <LoaderCircleIcon className='size-4 animate-spin' aria-hidden /> : <ArrowRightIcon className='size-4' aria-hidden />}</span>
                </button>
              </form>
              <p id='axm-note' className='axm-note axm-rise' style={at(0.26)}>
                <b>Open demo, no sign-in.</b> Plain fetch only · one homepage · up to 25 s · {config.perVisitor} runs per visitor per hour.
                {config.disabled ? ' Live runs are switched off by the site owner right now.' : ''}
                {!config.disabled && !config.modelReady ? ` No model key is set on the server yet, so runs fetch and clean but skip extraction.` : ''}
              </p>

              <div aria-live='polite' role='status' className='w-full'>
                {phase === 'error' && error ? <p className='axm-alert' role='alert'><b>Not run.</b> {error}</p> : null}
                {phase === 'done' && result && dec ? (
                  <div className='axm-result'>
                    <div className='top'>
                      <span className={cn('axm-pill', dec)}>{DECISION[dec].label}</span>
                      <span className='dom'>{result.domain}{result.from_cache ? ' · from cache' : ''}</span>
                      {result.saved ? <Link className='axm-link' href={result.record_path}>Open the full record</Link> : null}
                    </div>
                    <span className='why'>{result.failure ? 'Flagged, not guessed: ' : ''}{clip(result.failure ? result.explanation ?? result.defence : result.defence, 200)}</span>
                  </div>
                ) : null}
              </div>
            </div>

            <div className='axm-stage axm-inset'>
              <div role='group' aria-label='Scraper stages' className='axm-card' onPointerEnter={() => setHeld(true)} onPointerLeave={() => setHeld(false)} onFocus={() => setHeld(true)} onBlur={() => setHeld(false)}>
                <div className='axm-card-head'>
                  <div className='axm-qwrap'>
                    <AnimatePresence mode='wait' initial={false}>
                      <motion.p key={`${stage.id}-${example}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.25, ease: EASE }} className='axm-card-question'>
                        <span className='axm-q'>Q.</span><span>{stage.question}</span>
                      </motion.p>
                    </AnimatePresence>
                    <span className={cn('axm-badge', !example && 'live')}>{example ? 'Example run, acme.com' : busy ? `Running ${shownDomain}` : shownDomain}</span>
                  </div>
                  <div className='axm-stepper'>
                    <button type='button' className='axm-step-btn' onClick={() => pick(active - 1)} aria-label='Previous stage'><ChevronLeftIcon className='size-4' aria-hidden /></button>
                    <p aria-live='polite' className='axm-step-label'>
                      <span>{stage.tab}</span><span className='n'>{active + 1}/{STAGES.length}</span>
                      <span className='axm-dots' aria-hidden>{stages.map((s, i) => <span key={s.id} className='axm-dot' data-s={s.state} data-cur={i === active} />)}</span>
                    </p>
                    <button type='button' className='axm-step-btn' onClick={() => pick(active + 1)} aria-label='Next stage'><ChevronRightIcon className='size-4' aria-hidden /></button>
                  </div>
                </div>

                <div className='axm-figure'>
                  <AnimatePresence mode='wait' initial={false}>
                    <motion.div key={`${stage.id}-${example}`} initial={{ opacity: 0, y: 8, filter: 'blur(6px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }} exit={{ opacity: 0, y: -6, filter: 'blur(6px)' }} transition={{ duration: 0.3, ease: EASE }}>
                      {stage.state === 'wait' && !stage.lines.length ? (
                        <p className='axm-wait'><LoaderCircleIcon className='size-4' aria-hidden /> Working on this stage…</p>
                      ) : stage.lines.length ? (
                        <ul className='axm-lines'>
                          {stage.lines.map((l, i) => (
                            <motion.li key={i} className='axm-line' data-t={l.tone ?? ''} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: reduce ? 0 : 0.05 + i * 0.07, duration: 0.3, ease: EASE }}>
                              <span className='k'>{l.k}</span>
                              <span className='v'>{l.v}{l.quote ? <> <mark>“{l.quote}”</mark></> : null}</span>
                            </motion.li>
                          ))}
                        </ul>
                      ) : (
                        <p className='axm-wait'>Waiting for the earlier stages to finish.</p>
                      )}
                    </motion.div>
                  </AnimatePresence>
                </div>

                {stage.facts.length ? (
                  <dl className='axm-facts' style={{ margin: 0 }}>
                    {stage.facts.map(f => <div key={f.label} className='axm-fact'><dt className='l'>{f.label}</dt><dd className='v' style={{ margin: 0 }}>{f.value}</dd></div>)}
                  </dl>
                ) : null}

                <div className='axm-card-foot'>
                  <span className='axm-insight-dot' aria-hidden />
                  <AnimatePresence mode='wait' initial={false}>
                    <motion.p key={`${stage.id}-${example}-${stage.state}`} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.22 }} className='axm-insight'>
                      {stage.insight}
                    </motion.p>
                  </AnimatePresence>
                </div>
                {phase === 'done' && result ? (
                  <p className='axm-spend'>
                    {result.model.calls} model call{result.model.calls === 1 ? '' : 's'} this run · shared daily budget {result.guards.budget_limit == null ? 'not set' : `${result.guards.budget_used} of ${result.guards.budget_limit}`} · Hunter is not used by live runs · took {result.elapsed_seconds.toFixed(1)}&nbsp;s.
                    {result.flags.length ? ` Flags: ${result.flags.join(', ')}.` : ''}
                  </p>
                ) : null}
              </div>
            </div>
          </section>
        </div>
      </div>
    </MotionConfig>
  )
}
