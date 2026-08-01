/**
 * The engine build stamp in the profile panel (M6 piece B, trap T1).
 *
 * The regression this exists for: the platform frontend BUNDLES the engine at
 * build time, so an engine change does not reach `/charts` until
 * `frontend/dist` is rebuilt, and nothing said so. On 2026-07-30 the editor and
 * the chart disagreed for ~14 hours and neither was "wrong".
 *
 * Two properties are load-bearing here, and both are about NOT MISLEADING:
 *
 *  1. The stamp is a PANEL-level fact, so it must render even with no
 *     indicators — that is exactly the state you are in while wondering whether
 *     the bundle is current.
 *  2. A dev bundle must SAY dev. A fallback that read like a real build identity
 *     would be believed, and a stamp that can lie about which code is running is
 *     worse than no stamp.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BUILD_STAMP } from '@openalgo/openscript'
import { ProfilePanel } from './ProfilePanel'

const noProfile = () => undefined

describe('ProfilePanel build stamp', () => {
  it('renders the engine build row even when no indicator is loaded', () => {
    render(<ProfilePanel indicators={[]} profileOf={noProfile} onClose={() => {}} />)
    expect(screen.getByText('No indicators on this chart.')).toBeInTheDocument()
    expect(screen.getByText('engine build')).toBeInTheDocument()
  })

  it('shows the stamp the bundled engine actually reports', () => {
    // Asserted against the engine's own value rather than a hardcoded string:
    // hardcoding would pin the tester's build and pass for the wrong reason.
    render(<ProfilePanel indicators={[]} profileOf={noProfile} onClose={() => {}} />)
    const expected = BUILD_STAMP.isDevBuild ? /dev build/i : new RegExp(BUILD_STAMP.fingerprint)
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('never renders something hash-like for a dev bundle', () => {
    render(<ProfilePanel indicators={[]} profileOf={noProfile} onClose={() => {}} />)
    if (!BUILD_STAMP.isDevBuild) return
    // The failure mode: a plausible-looking short sha that gets trusted.
    expect(screen.queryByText(/^[0-9a-f]{7}\b/)).toBeNull()
  })

  it('does not claim to detect staleness', () => {
    // The tooltip is the only place this panel explains itself, and overclaiming
    // there is how a build stamp gets mistaken for a freshness check.
    const { container } = render(
      <ProfilePanel indicators={[]} profileOf={noProfile} onClose={() => {}} />
    )
    const row = container.querySelector('[title]')
    expect(row).not.toBeNull()
    const title = row?.getAttribute('title') ?? ''
    expect(title).not.toMatch(/\bstale\b(?!ness)/i)
    if (!BUILD_STAMP.isDevBuild) expect(title).toMatch(/cannot detect staleness/i)
  })
})
