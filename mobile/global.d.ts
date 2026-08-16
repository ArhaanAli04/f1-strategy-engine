// Ambient module declaration for CSS imports (app/_layout.tsx's
// `import "../global.css"`). CI's mobile-check job (ubuntu-latest, fresh
// `npm ci`) failed on this import with "Cannot find module './global.css'
// or its corresponding type declarations", while `nativewind-env.d.ts`'s
// `/// <reference types="nativewind/types" />` alone was enough locally
// (Windows) — same TypeScript/nativewind versions either way (locked via
// package-lock.json), and confirmed nativewind's package.json has no
// `exports` field to cause an exports-map/subpath resolution difference,
// so the precise local-vs-CI divergence in how that reference resolves
// wasn't pinned down. This sidesteps it rather than chasing it further: a
// plain ambient wildcard declaration satisfies `import "../global.css"`
// directly via TypeScript's own declaration merging, independent of
// whether `nativewind/types` resolves at all.
declare module "*.css" {
  const content: string;
  export default content;
}
