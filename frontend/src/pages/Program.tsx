import { useEffect, useState } from "react";
import {
  type ApiClient,
  type MonthSummary,
  type ProgramGrid,
} from "../api/client";
import { MonthSelector } from "../components/MonthSelector";
import { ProgramMatrix } from "../components/ProgramMatrix";

export interface ProgramProps {
  api: ApiClient;
  months: MonthSummary[];
  monthsError: string | null;
}

type Perspective = "stores" | "people";

export function Program({ api, months, monthsError }: ProgramProps) {
  const [monthId, setMonthId] = useState<string | null>(months[0]?.id ?? null);
  const [perspective, setPerspective] = useState<Perspective>("stores");
  const [grid, setGrid] = useState<ProgramGrid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewportHeight, setViewportHeight] = useState(480);

  useEffect(() => {
    setMonthId((current) => current ?? months[0]?.id ?? null);
  }, [months]);

  useEffect(() => {
    function onResize() {
      if (window.innerWidth < 720) {
        setViewportHeight(320);
      } else if (window.innerWidth < 1100) {
        setViewportHeight(400);
      } else {
        setViewportHeight(560);
      }
    }
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!monthId) {
      setGrid(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .get<ProgramGrid>(`/months/${monthId}/program?perspective=${perspective}`)
      .then((response) => {
        if (!cancelled) setGrid(response);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [api, monthId, perspective]);

  return (
    <section className="card" aria-label="Program">
      <header className="card-header">
        <h2>Program</h2>
        <div className="program-toolbar">
          <MonthSelector
            months={months}
            value={monthId}
            onChange={setMonthId}
            error={monthsError}
          />
          <fieldset className="perspective-switch" aria-label="Perspectivă">
            <legend className="muted">Perspectivă</legend>
            <label>
              <input
                type="radio"
                name="perspective"
                value="stores"
                checked={perspective === "stores"}
                onChange={() => setPerspective("stores")}
              />
              Per magazin
            </label>
            <label>
              <input
                type="radio"
                name="perspective"
                value="people"
                checked={perspective === "people"}
                onChange={() => setPerspective("people")}
              />
              Per agent
            </label>
          </fieldset>
        </div>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {grid && (
        <ProgramMatrix grid={grid} viewportHeight={viewportHeight} />
      )}
    </section>
  );
}
