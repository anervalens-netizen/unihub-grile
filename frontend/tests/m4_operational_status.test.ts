import { describe, expect, it } from "vitest";
import {
  auditActionLabel,
  dataFreshnessLabel,
  healthStateLabel,
  monthStateLabel,
  queueSemanticTone,
  queueStateLabel,
  queueStateTone,
  type QueueState,
} from "../src/operationalStatus";

describe("FE-012 operational status contract", () => {
  it("uses one Romanian vocabulary for month and audit states", () => {
    expect(monthStateLabel("OPEN")).toBe("Deschisă");
    expect(monthStateLabel("CLOSED")).toBe("Închisă");
    expect(monthStateLabel("REOPENED")).toBe("Redeschisă");
    expect(auditActionLabel("CLOSE")).toBe("Închidere");
    expect(auditActionLabel("REOPEN")).toBe("Redeschidere");
  });

  it("keeps queue labels and colors semantically aligned", () => {
    const cases: Array<[QueueState, string, string, string]> = [
      ["QUEUED", "În așteptare", "checking", "warn"],
      ["RETRY", "Reîncercare", "checking", "warn"],
      ["RUNNING", "În execuție", "checking", "warn"],
      ["FAILED", "Eșuat", "offline", "err"],
      ["DONE", "Finalizat", "online", "ok"],
    ];
    for (const [state, label, dotTone, semanticTone] of cases) {
      expect(queueStateLabel(state)).toBe(label);
      expect(queueStateTone(state)).toBe(dotTone);
      expect(queueSemanticTone(state)).toBe(semanticTone);
    }
  });

  it("uses consistent data freshness and system-health wording", () => {
    expect(dataFreshnessLabel(true)).toBe("Actualizat");
    expect(dataFreshnessLabel(false)).toBe("Neactualizat");
    expect(healthStateLabel("online")).toBe("Disponibil");
    expect(healthStateLabel("checking")).toBe("Verificare");
    expect(healthStateLabel("offline")).toBe("Indisponibil");
  });
});
