import { cpSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(root, "node_modules", "@fontsource-variable", "manrope", "files");
// Tailwind emits the stylesheet to static_dist/css/app.css and @fontsource writes its
// @font-face rules as url(./files/…), a path relative to that stylesheet. The fonts therefore
// have to sit next to it, not at the root of static_dist, or every face resolves to a 404.
const destination = join(root, "static_dist", "css", "files");

mkdirSync(destination, { recursive: true });
for (const file of readdirSync(source).filter((name) => name.startsWith("manrope-") && name.endsWith(".woff2"))) {
  cpSync(join(source, file), join(destination, file));
}

// Static assets Tailwind and esbuild do not emit themselves.
cpSync(join(root, "assets", "favicon.svg"), join(root, "static_dist", "favicon.svg"));
