# UniHub Grile — agent execution rules

UniHub Grile se dezvoltă acum ca **standalone plugin candidate** pentru o
integrare ulterioară în UniHub Retail.

## 1. Start obligatoriu

Înainte de orice modificare:

1. citește issue #3 — `PROGRAM PLAN — UniHub Grile 8.5+ Standalone Plugin Candidate`;
2. citește issue #4 — `MASTER TRACKER — UniHub Grile to Server-Test Ready`;
3. citește documentele canonice relevante pentru task;
4. verifică `main`, PR-urile active și schimbările mai noi;
5. selectează numai taskuri READY/neconflictuale din tracker.

Conversațiile, vechile stage labels sau documentele istorice nu sunt surse de
status. Issue #4 este trackerul unic.

## 2. Boundary Retail

Până când utilizatorul deschide explicit milestone-ul de integrare:

- `anervalens-netizen/unihub-retail` este **read-only**;
- poate fi inspectat pentru arhitectură, contracte, auth, capabilities, UI și
  modele de date;
- nu se creează commit/branch/PR în Retail;
- nu se modifică runtime, DB, servicii, fișiere sau deployment Retail;
- Grile nu importă pachete/source Retail la runtime;
- nu se construiește o dependență directă permanentă pe schema DB Retail.

Toată compatibilitatea se implementează aici prin contracte/adaptoare.

## 3. Source-of-truth

Ordine de autoritate:

1. issue #3 — scop, milestones, ordine și gate final;
2. issue #4 — taskuri și status;
3. `docs/PRODUCT_CONTRACT.md` — comportament business;
4. `ARCHITECTURE.md` — boundaries și structură;
5. `docs/MOBIUP_RULE_PACK.md` — formule/politici Mobiup;
6. `docs/UX_EXCEL_SPEC.md` — UX/Google/XLSX;
7. `docs/RETAIL_INTEGRATION_CONTRACT.md` — contract viitor Retail;
8. `docs/QUALITY_GATES.md` — dovezi și scorare.

Dacă două documente par incompatibile, nu ghici. Respectă sursa cu autoritate
mai mare și actualizează documentația conflictuală în același batch.

## 4. Workflow GitHub

Pentru modificări materiale:

- lucrează pe branch/PR focalizat;
- PR-ul trebuie să enumere task IDs din issue #4;
- descrie contractele/invariantele schimbate;
- enumeră comenzile/testele efectiv rulate;
- menționează explicit ce NU a putut fi verificat;
- nu merge-ui cu mandatory checks failing;
- după merge, actualizează issue #4 cu evidence și următorul READY.

Nu crea trackere, handoff-uri sau roadmap-uri paralele. Un subplan temporar este
permis doar dacă issue #3/#4 îl cer explicit și trebuie să trimită înapoi statusul
în trackerul principal.

## 5. Evidence și PASS

Un task devine `[x]` numai când există dovadă adecvată tipului de schimbare.

Exemple:
- domain rule → unit/golden/property tests;
- DB invariant/concurrency → PostgreSQL real;
- auth/scope → positive + negative resource-level tests;
- API → contract/integration tests;
- frontend behavior → component tests + browser/runtime proof când este necesar;
- XLSX → workbook parsing, nu doar existența fișierului;
- Google → fake adapter structural suite și bounded canary când gate-ul îl cere;
- performance → măsurătoare cu fixture reprezentativ;
- deployment/ops → runtime probe, nu inferență din config.

Nu declara browser validation dacă nu există browser tool/runner. Nu declara
server/production readiness din build/test local.

## 6. Product invariants

- max. un agent lucrător per magazin/zi;
- max. un magazin lucrat per persoană/zi;
- `EXTRA_HOME` numai în home store;
- `EXTRA_OTHER` numai într-un alt store;
- calendarul este autoritatea pentru pontaj și credit personal;
- vânzarea fizică magazin/zi nu se dublează prin reatribuire;
- Google Sheet nu este engine/baza financiară;
- closed month respinge writes;
- reopen este motivat/admin-only/auditat;
- inputurile financiare obligatorii lipsă/stale nu devin rezultat final valid;
- business mutations relevante au audit append-only;
- tenant + resource scope se verifică backend, centralizat.

## 7. Architecture invariants

- API → services → domain → repositories/connectors;
- domain-ul nu depinde de FastAPI/Google/Retail;
- Retail-specific mapping stă în adapters/contracts;
- Mobiup-specific constants stau în rule pack;
- Google/XLSX heavy I/O rulează async;
- last-good external projection nu este distrusă de un failed attempt;
- joburile trebuie să devină recoverable/idempotent înainte de server-test gate.

## 8. Security și date

Nu commit-ui:
- credentials/tokens/secrets;
- Google Sheet IDs live;
- CNP, emailuri reale sau alte date personale inutile;
- date salariale reale neanonimizate;
- dumpuri production;
- screenshoturi cu informații personale.

Fixtures pentru test/reconciliere trebuie să fie sintetice sau anonimizate.

## 9. Modificări concurente

Dacă există mai mulți builders:

- folosiți branch-uri separate;
- ownership non-overlapping pe task/file boundary;
- nu forțați ref-uri peste munca altuia;
- rebase/merge numai după verificarea driftului;
- update-ul trackerului se face după merge-ul real, nu înainte.

## 10. Stagnation

Nu repeta aceeași abordare eșuată fără informație nouă.

După două încercări fără progres măsurabil:
1. documentează cauza în PR/issue;
2. replănuiește o dată;
3. dacă rămâne blocat, marchează taskul BLOCKED cu condiția exactă de unblock.

## 11. Prioritate

Ordinea implicită este:

1. P0/P1 correctness, authorization, data-loss, financial close;
2. runtime resilience;
3. API/data performance;
4. frontend operational completeness;
5. Google/XLSX productionization;
6. Retail adapter package;
7. CI/observability;
8. reconciliation și candidate gate.

Polish-ul vizual nu preemptează un defect de scope sau calcul.

## 12. Definition of done al programului

Programul curent se încheie numai când `SERVER-TEST-READY` din issue #4 este
bifat pe un commit exact, cu criteriile din `docs/QUALITY_GATES.md` îndeplinite.

Abia după aceea se poate deschide un program separat care modifică efectiv
UniHub Retail.
