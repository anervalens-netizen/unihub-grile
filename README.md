# UniHub Grile

Aplicație standalone pentru program, pontaj, suplimentare, atribuirea vânzărilor,
grile salariale și proiecția lor în Google Sheets.

Stare curentă: **S1 PASS; S2 PASS; S3 PASS** (AC-07/08/09/15 pe exact candidate `7b96f20`).
**S4 BUILDING** (UI manager complet; AC-10/04/11/15 slices la cod PASS, AC-16 perf noise intermittent).
**S5 BUILDING** (fake Google adapter + E-pay + XLSX exports; AC-13/14/15 slices la cod PASS, AC-12 lipsă probe independente end-to-end, AC-14 export defecte reale — vezi `docs/exec-plans/active/UGR-001-STANDALONE-GRILE.md`).
S6/S7 BACKLOG (shadow pilot, Retail integration).

## Principiul de bază

Managerul lucrează în aplicație. Agentul primește Google Sheet-ul magazinului,
cu grila și calendarul în mod funcțional read-only. Singurele intrări permise în
Sheet sunt cele două cantități E-pay pentru fiecare agent.

```text
Retail sau alt sistem client
          |
          | contract versionat: magazine, persoane, vânzări, targete
          v
     UniHub Grile
       |      |
       |      +--> Manager UI: calendar, excepții, close, import/export
       |
       +---------> Google Sheets: proiecție + input E-pay restrâns
```

## Documente canonice

- [Arhitectură](ARCHITECTURE.md)
- [Contract produs și reguli business](docs/PRODUCT_CONTRACT.md)
- [Reguli Mobiup: grilă și Pontaj](docs/MOBIUP_RULE_PACK.md)
- [UX, import și export Excel](docs/UX_EXCEL_SPEC.md)
- [Tracker activ](docs/exec-plans/active/UGR-001-STANDALONE-GRILE.md)
- [Reguli ExecPlan](.agent/PLANS.md)

## Pentru agentul care implementează

Nu porni din conversații. Citește documentele de mai sus, apoi implementează
numai etapa marcată `READY` în tracker. Predă commitul exact, comenzile și
rezultatele cerute de acea etapă. Evaluarea GO/NO-GO se face ulterior, read-only.
