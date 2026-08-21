# UniHub Grile

Aplicație standalone pentru program, pontaj, suplimentare, atribuirea vânzărilor,
grile salariale și proiecția lor în Google Sheets.

Stare curentă: **S1–S5 PASS; S6 READY; S7 BACKLOG**.

Stackul local pornește determinist `migrate → API/worker → web`, fără instalări
de pachete la runtime. Fluxul fixture verificat este Program → Pontaj → calcul
grilă → export asincron → polling → download XLSX. Google live și Retail rămân
neatinse; canary-ul Google copiat se autorizează separat.

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
