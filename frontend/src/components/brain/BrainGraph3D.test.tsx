import { describe, it, expect } from 'vitest'
import BrainGraph3D from './BrainGraph3D'

/**
 * Import smoke test for the lazy-loaded 3D view.
 *
 * The brain page lazy-imports this module only when the user switches to 3D,
 * so a broken module graph (e.g. `three` or `3d-force-graph` removed from
 * package.json) fails in the browser as a blocked dynamic import — exactly
 * the NS_ERROR_CORRUPTED_CONTENT failure seen on the brain page. Importing the
 * module here forces three.js + 3d-force-graph to resolve at test time.
 */
describe('BrainGraph3D module', () => {
  it('resolves its dependencies and exports the component', () => {
    expect(typeof BrainGraph3D).toBe('function')
  })
})