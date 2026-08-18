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

afterEach(() => {
  cleanup()
})
