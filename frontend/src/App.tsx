import { Navigate, NavLink, Route, Routes, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Sport } from "./lib/api";
import { SportRail } from "./components/SportRail";
import { Slate } from "./pages/Slate";
import { Edges } from "./pages/Edges";
import { Teams } from "./pages/Teams";
import { Pitchers } from "./pages/Pitchers";
import { Mismatches } from "./pages/Mismatches";
import { Markets } from "./pages/Markets";
import { NflSplits } from "./pages/NflSplits";
import { NflSlate } from "./pages/NflSlate";
import { NflRatings } from "./pages/NflRatings";
import { NflFactors } from "./pages/NflFactors";
import { NflMovement } from "./pages/NflMovement";
import { NflProps } from "./pages/NflProps";
import { NflScan } from "./pages/NflScan";
import { GameDetail } from "./pages/GameDetail";

function NflShell() {
  return (
    <>
      <div className="subnav">
        <NavLink end to="/nfl" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Splits
        </NavLink>
        <NavLink to="/nfl/schedule" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Schedule
        </NavLink>
        <NavLink to="/nfl/scan" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Scan
        </NavLink>
        <NavLink to="/nfl/props" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Props
        </NavLink>
        <NavLink to="/nfl/movement" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Movement
        </NavLink>
        <NavLink to="/nfl/factors" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Factors
        </NavLink>
        <NavLink to="/nfl/ratings" className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Ratings
        </NavLink>
      </div>
      <Routes>
        <Route index element={<NflSplits />} />
        <Route path="schedule" element={<NflSlate />} />
        <Route path="scan" element={<NflScan />} />
        <Route path="props" element={<NflProps />} />
        <Route path="movement" element={<NflMovement />} />
        <Route path="factors" element={<NflFactors />} />
        <Route path="ratings" element={<NflRatings />} />
      </Routes>
    </>
  );
}

function SportShell() {
  const { sport } = useParams();
  const s = (sport === "nba" ? "nba" : "mlb") as Sport;

  return (
    <>
      <div className="subnav">
        <NavLink end to={`/${s}`} className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Slate
        </NavLink>
        <NavLink to={`/${s}/edges`} className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Edges
        </NavLink>
        <NavLink to={`/${s}/teams`} className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}>
          Teams
        </NavLink>
        {s === "mlb" && (
          <>
            <NavLink
              to="/mlb/mismatches"
              className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}
            >
              Mismatches
            </NavLink>
            <NavLink
              to="/mlb/markets"
              className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}
            >
              Markets
            </NavLink>
            <NavLink
              to="/mlb/pitchers"
              className={({ isActive }) => `subnav-link${isActive ? " active" : ""}`}
            >
              Pitchers
            </NavLink>
          </>
        )}
      </div>
      <Routes>
        <Route index element={<Slate sport={s} />} />
        <Route path="edges" element={<Edges sport={s} />} />
        <Route path="teams" element={<Teams sport={s} />} />
        <Route path="mismatches" element={<Mismatches />} />
        <Route path="markets" element={<Markets />} />
        <Route path="pitchers" element={<Pitchers />} />
      </Routes>
    </>
  );
}

export default function App() {
  const { data: odds } = useQuery({
    queryKey: ["odds"],
    queryFn: api.oddsStatus,
    refetchInterval: 120_000,
  });

  return (
    <>
      <SportRail odds={odds} />
      <div className="shell">
        <Routes>
          {/* NFL is the priority sport in season; the others are secondary. */}
          <Route path="/" element={<Navigate to="/nfl" replace />} />
          <Route path="/game/:id" element={<GameDetail />} />
          <Route path="/nfl/*" element={<NflShell />} />
          <Route path="/:sport/*" element={<SportShell />} />
        </Routes>
      </div>
    </>
  );
}
