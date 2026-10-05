/**
 * Colour contrast and focus visibility, computed from the stylesheet.
 *
 * The workspace declares every text colour as a foreground/background pair in
 * `globals.css`. This parses those declarations and applies the WCAG contrast
 * formula, so a colour that fails accessibility fails the build - without a
 * browser, and on every commit rather than whenever someone remembers to check.
 *
 * A browser can confirm the computed result on a real page; only this can
 * confirm it for every pair, every time.
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssPath = new URL("../src/app/globals.css", import.meta.url);

/** WCAG 2.1 relative luminance. */
function luminance(hex) {
  const value = hex.replace("#", "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((character) => character + character)
          .join("")
      : value;
  const channels = [0, 2, 4].map((offset) => {
    const srgb = Number.parseInt(full.slice(offset, offset + 2), 16) / 255;
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrastRatio(foreground, background) {
  const a = luminance(foreground);
  const b = luminance(background);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

async function tokens() {
  const css = await readFile(cssPath, "utf8");
  const found = new Map();
  for (const match of css.matchAll(/--(ws-[a-z-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    found.set(match[1], match[2]);
  }
  return found;
}

test("the stylesheet declares the workspace palette", async () => {
  const found = await tokens();
  assert.ok(found.size >= 20, `only ${found.size} workspace tokens found`);
  assert.ok(found.has("ws-fg") && found.has("ws-bg"));
});

test("every declared foreground meets WCAG AA on its own background", async () => {
  const found = await tokens();
  const pairs = [];

  for (const [name, value] of found) {
    if (!name.endsWith("-fg")) {
      continue;
    }
    const backgroundName = `${name.slice(0, -3)}-bg`;
    const background = found.get(backgroundName);
    assert.ok(background, `${name} has no matching ${backgroundName}`);
    pairs.push([name, contrastRatio(value, background)]);
  }

  assert.ok(pairs.length >= 10, `only ${pairs.length} foreground pairs checked`);
  for (const [name, ratio] of pairs) {
    assert.ok(
      ratio >= 4.5,
      `${name} contrast is ${ratio.toFixed(2)}:1, below the 4.5:1 AA minimum for text`,
    );
  }
});

test("the contrast maths matches known WCAG values", async () => {
  // Black on white is 21:1; a mid grey on white is close to 4.54:1. Without
  // this, a broken formula would pass everything.
  assert.equal(Math.round(contrastRatio("#000000", "#ffffff")), 21);
  assert.equal(Math.round(contrastRatio("#ffffff", "#ffffff")), 1);
  assert.ok(Math.abs(contrastRatio("#767676", "#ffffff") - 4.54) < 0.05);
});

test("a deliberately poor pair would be caught", async () => {
  assert.ok(contrastRatio("#aaaaaa", "#ffffff") < 4.5);
  assert.ok(contrastRatio("#d1d5db", "#ffffff") < 4.5);
});

test("borders are visible against their surface", async () => {
  const found = await tokens();
  // 3:1 is the WCAG minimum for meaningful non-text boundaries.
  const ratio = contrastRatio(found.get("ws-border-strong"), found.get("ws-bg"));
  assert.ok(ratio >= 3, `strong border contrast is ${ratio.toFixed(2)}:1, below 3:1`);
});

test("every interactive element has a visible focus ring", async () => {
  const css = await readFile(cssPath, "utf8");
  const block = css.slice(css.indexOf(".ws a:focus-visible"));
  const selectors = block.slice(0, block.indexOf("{"));

  for (const element of ["a", "button", "input", "select", "summary"]) {
    assert.match(selectors, new RegExp(`\\.ws ${element}:focus-visible`), `${element} has no focus style`);
  }
  assert.match(block.slice(0, 400), /outline:\s*3px solid/);
  assert.match(block.slice(0, 400), /outline-offset/);
});

test("focus is never removed without a replacement", async () => {
  const css = await readFile(cssPath, "utf8");
  for (const match of css.matchAll(/outline:\s*(none|0)\s*;/g)) {
    const context = css.slice(Math.max(0, match.index - 200), match.index);
    assert.match(
      context,
      /:focus\b|\.ws-main/,
      "outline removed outside the main landmark, which would hide focus",
    );
  }
});

test("the skip link becomes visible when focused", async () => {
  const css = await readFile(cssPath, "utf8");
  assert.match(css, /\.ws-skip\s*\{[^}]*left:\s*-9999px/);
  assert.match(css, /\.ws-skip:focus\s*\{[^}]*left:/);
});

test("the layout adapts to narrow screens", async () => {
  const css = await readFile(cssPath, "utf8");
  const queries = [...css.matchAll(/@media \(max-width: ([\d.]+)rem\)/g)].map((m) => Number(m[1]));
  assert.ok(queries.some((width) => width <= 48), "no phone-width breakpoint");
  assert.ok(queries.some((width) => width > 48), "no tablet-width breakpoint");

  // The seven-column table has to become something usable on a phone.
  const narrow = css.slice(css.indexOf("@media (max-width: 48rem)"));
  assert.match(narrow, /\.ws-table thead\s*\{\s*display:\s*none/);
});

test("navigation and view switches are big enough to tap", async () => {
  const css = await readFile(cssPath, "utf8");
  const block = css.slice(css.indexOf("/* Navigation and view switches are tapped"));
  const rule = block.slice(0, block.indexOf("}") + 1);

  for (const selector of [".ws-nav a", ".ws-viewlink", ".ws-pagination a"]) {
    assert.ok(rule.includes(selector), `${selector} has no minimum target size`);
  }
  const minHeight = Number(rule.match(/min-height:\s*([\d.]+)rem/)?.[1] ?? 0);
  assert.ok(minHeight * 16 >= 24, `target height is ${minHeight * 16}px, below the 24px minimum`);
});

test("reduced motion is respected", async () => {
  const css = await readFile(cssPath, "utf8");
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});
