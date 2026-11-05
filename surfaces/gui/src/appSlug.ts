import { pinyin } from "pinyin-pro";

export interface SlugOptions {
  allowTrailingDash?: boolean;
}

/**
 * Converts any user input (English, Chinese, mixed, numbers, etc.) into a valid app slug:
 * - Lowercase alphanumeric characters and single hyphens: ^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$
 * - Converts Chinese characters into Pinyin (without tone marks)
 * - Replaces spaces and underscores with single hyphens
 * - Ensures slug starts with a letter [a-z] (prefixes 'app-' if starting with digit)
 * - Slices to max 40 characters
 */
export function toAppSlug(input: string, options: SlugOptions = {}): string {
  if (!input) return "";

  // Convert Chinese to pinyin, keeping non-Chinese words intact
  const py = pinyin(input, { toneType: "none", nonZh: "consecutive" });

  let res = py
    .toLowerCase()
    .trim()
    .replace(/[\s_]+/g, "-")          // spaces / underscores -> dash
    .replace(/[^a-z0-9-]/g, "")        // remove illegal characters
    .replace(/-+/g, "-")               // collapse duplicate dashes
    .replace(/^-+/g, "");              // remove leading dashes

  // Backend requires slug to start with [a-z]: ^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$
  if (/^[0-9]/.test(res)) {
    res = `app-${res}`;
  }

  if (!options.allowTrailingDash) {
    res = res.replace(/-+$/g, "");
  }

  // CapRover & delivery.SLUG length constraint: 3 <= len <= 40
  return res.slice(0, 40);
}
