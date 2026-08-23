/** Palette switcher, sitting in the rail beside the credits chip.
 *
 * Here to be used and then removed. Three schemes are live so they can be compared on real
 * data rather than in a mockup — a palette that looks fine on a swatch can still fail on a
 * dense table, which is how the original ended up with 2.89:1 secondary text. Once one
 * wins, the losers and this control come out.
 */
import { useEffect, useState } from "react";

const THEMES = [
  { key: "slate", label: "SLATE", hint: "Neutral chrome, colour reserved for data" },
  { key: "terminal", label: "TERMINAL", hint: "The original phosphor green" },
  { key: "daylight", label: "DAYLIGHT", hint: "Light: paper and ink" },
];

const STORAGE_KEY = "edgebetter-theme";

export function ThemeSwitch() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem(STORAGE_KEY) ?? "slate",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(STORAGE_KEY, theme);
    // Keep the browser chrome in step with the page, or the address bar on mobile stays
    // dark above a light theme.
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--bg")
      .trim();
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", bg || "#0c1116");
  }, [theme]);

  return (
    <div className="theme-switch" role="group" aria-label="Colour scheme">
      {THEMES.map((t) => (
        <button
          key={t.key}
          type="button"
          title={t.hint}
          aria-pressed={theme === t.key}
          className={`theme-chip${theme === t.key ? " active" : ""}`}
          onClick={() => setTheme(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
