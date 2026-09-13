# Creative Manager UI foundation

`tokens.css` is the canonical visual contract for brand colors, gradients, semantic colors,
surfaces, radii, and elevation. `components.tsx` contains deliberately small presentation
primitives; domain behavior remains in the Product Workspace.

The approved Creative Manager artwork lives under `apps/web/public/brand/`. `BrandMark` uses the
dark app icon for compact navigation surfaces and retains the transparent standalone mark for
larger unframed placements. `BrandLockup` uses the horizontal light-surface lockup on the access
screen and pairs the app icon with readable text in the dark sidebar. The supplied app icon is
also the browser and Apple touch icon.

Keep the component APIs and accessible names stable if the raster artwork is replaced with final
production SVG exports later.
