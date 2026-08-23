/** The thin line of sports at the very top. Nothing sits above it. */
import { NavLink } from "react-router-dom";
import type { OddsStatus } from "../lib/api";
import { ThemeSwitch } from "./ThemeSwitch";

const SPORTS = [
  { key: "mlb", label: "BASEBALL" },
  { key: "nba", label: "BASKETBALL" },
  { key: "nfl", label: "FOOTBALL" },
];

export function SportRail({ odds }: { odds?: OddsStatus }) {
  const dot = !odds?.enabled ? "warn" : odds.last_poll_ok === false ? "warn" : "live";
  const label = !odds?.enabled
    ? "NO ODDS KEY"
    : odds.credits_remaining != null
      ? `${odds.credits_remaining} CREDITS`
      : "ODDS READY";

  return (
    <nav className="rail" aria-label="Sports">
      <div className="rail-mark">EDGEBETTER</div>
      {SPORTS.map((s) => (
        <NavLink
          key={s.key}
          to={`/${s.key}`}
          className={({ isActive }) => `rail-link${isActive ? " active" : ""}`}
        >
          {s.label}
        </NavLink>
      ))}
      <ThemeSwitch />
      <div className="rail-status" title={odds?.reason}>
        <span className={`rail-dot ${dot}`} />
        {label}
      </div>
    </nav>
  );
}
