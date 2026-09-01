import { cleanup } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"

// jsdom has no window.matchMedia implementation — LiveTimingTower (and any
// future prefers-reduced-motion check) needs this stubbed to render at all.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// jsdom has no scrollIntoView implementation — Radix Select (SimulatorPage's
// Driver/Compound dropdowns) calls it internally when its content mounts, and
// an unstubbed call throws (candidate?.scrollIntoView is not a function),
// crashing the whole render. Behavior is irrelevant in tests (there's no
// real scrolling), so a no-op stub is enough.
Element.prototype.scrollIntoView = vi.fn()

afterEach(() => {
  cleanup()
})
