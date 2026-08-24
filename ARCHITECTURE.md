# Arhitectură UniHub Grile

## 1. Poziționare

UniHub Grile este dezvoltat acum ca aplicație standalone, dar **produsul țintă
este un modul al UniHub Retail**.

Separarea actuală este intenționată: permite dezvoltare, refactorizare, testare,
reconciliere și schimbări de schemă fără risc asupra aplicației Retail aflate în
alt flux de dezvoltare.

Până la finalizarea programului din issue #3:

- `unihub-retail` este sursă read-only de compatibilitate;
- Grile nu importă cod Retail la runtime;
- Grile nu scrie direct în schema PostgreSQL Retail;
- Grile nu modifică frontendul, auth-ul, serviciile sau deploy-ul Retail;
- toate adaptările necesare viitoarei integrări sunt dezvoltate în acest repo.

Integrarea reală începe numai după gate-ul `SERVER-TEST-READY` din issue #4 și
după o aprobare explicită separată.

## 2. Principiul de arhitectură

Domain-ul Grile trebuie să depindă de **contracte proprii stabile**, nu de locul
din care vin datele.

```text
            +----------------------+
            |  Fixture adapters    |
            |  Future Retail       |
            |  adapters            |
            +----------+-----------+
                       |
                 versioned DTOs
                       v
+--------------------------------------------------+
|                  UniHub Grile                    |
|                                                  |
|  API / auth boundary                             |
|          |                                       |
|          v                                       |
|  application services                            |
|          |                                       |
|          v                                       |
|  domain engine                                   |
|   - calendar / coverage                          |
|   - pontaj                                       |
|   - attribution                                  |
|   - grid / rule packs                            |
|   - close / reopen                               |
|          |                                       |
|          v                                       |
|  repositories -> PostgreSQL                      |
|          |                                       |
|          +--> outbox/jobs -> worker              |
|                            |        |             |
|                            v        v             |
|                     Google Sheets  XLSX           |
+--------------------------------------------------+
```

Aceeași regulă se aplică identității:

```text
PrincipalProvider
  |- DevelopmentPrincipalProvider
  `- FutureRetailPrincipalProvider

Principal
  |- user_id
  |- tenant_id
  |- roles
  |- capabilities
  `- effective resource scope
```

Niciun service business nu trebuie să depindă de header-ele de development sau
de mecanismul final de sesiune Retail.

## 3. Autorități de date

| Domeniu | Autoritate în Grile standalone | Autoritate după integrarea Retail |
|---|---|---|
| Tenant / timezone | connector/fixture | Retail contract |
| Magazine / ierarhie | connector/fixture | Retail contract |
| Persoane / home store | connector/fixture | Retail contract |
| Manager scopes | Grile fixture/master | Retail contract/effective scope |
| Vânzări magazin/zi | connector snapshot | Retail contract |
| Target / incentive external | connector snapshot | Retail contract |
| Program | Grile | Grile |
| Pontaj | derivat în Grile | derivat în Grile |
| Atribuire personală | derivată în Grile | derivată în Grile |
| E-pay | Grile după readback validat | Grile după readback validat |
| Rule pack / calcul | Grile | Grile |
| Close / reopen | Grile | Grile |
| Google Sheet | proiecție + input E-pay limitat | proiecție + input E-pay limitat |

**Missing input nu devine zero implicit** dacă poate influența salariul. Se
păstrează ultima generație bună sau se emite anomalie/blocker conform contractului.

## 4. Layere și responsabilități

### API

Responsabil:
- validare request/response;
- principal/capabilities;
- autorizare resource-level;
- mapping erori HTTP;
- correlation/request ID.

Nu conține formule de salariu și nu execută Google I/O sincron în requesturile
operaționale.

### Services

Responsabil:
- use cases și tranzacții;
- orchestrarea domain + repositories + connectors;
- revision/CAS;
- emiterea auditului și joburilor.

### Domain

Responsabil:
- reguli pure/deterministe;
- invariante de calendar;
- pontaj;
- atribuire;
- grid și rule pack;
- close policy.

Domain-ul nu cunoaște FastAPI, Google, Retail sau detalii de storage.

### Repositories

Responsabil:
- PostgreSQL persistence;
- constraints/indexes;
- snapshot/generation state;
- audit append-only;
- outbox/job persistence.

### Connectors / adapters

Responsabil:
- transformarea unei surse externe într-un contract Grile versionat;
- validare structurală;
- generații atomice și last-good semantics.

Aici vor exista ulterior adaptoarele Retail. Nu se împrăștie logică Retail în
services/domain.

### Worker

Responsabil:
- singura execuție autoritativă a joburilor asincrone;
- Google projection/readback;
- import/export mare;
- ingest snapshot;
- retry/recovery/idempotency.

În candidate-ul final, joburile trebuie să aibă recovery determinist după crash,
`run_after` respectat, retry bounded și stare vizibilă.

## 5. Model de domeniu

Modelul existent rămâne baza arhitecturii:

- `tenants`;
- `users`, roluri/capabilities și `manager_scopes`;
- `stores`;
- `people` + home store;
- effective-dated assignments/scopes;
- `months` cu state și revision;
- `site_day_assignments`;
- `person_day_absences`;
- `pontaj_projections`;
- `sales_store_day` și proiecția personală;
- targete și inputuri externe versionate;
- `epay_observations`;
- `grid_calculations` cu canonical inputs/hashes;
- `sheet_bindings` și projection runs;
- import/export runs;
- outbox/jobs;
- `audit_events` append-only.

Toate datele business sunt tenant-scoped. Resource scope-ul este o constrângere
de autorizare separată și trebuie aplicat central, nu ad-hoc pe endpoint.

## 6. Invariante business

1. Un magazin nu poate avea mai mult de un agent lucrător în aceeași zi.
2. O persoană nu poate lucra în două magazine în aceeași zi.
3. `EXTRA_HOME` cere magazinul de bază.
4. `EXTRA_OTHER` cere alt magazin decât cel de bază.
5. `OFF`/`LEAVE` nu ocupă acoperirea magazinului.
6. Calendarul este autoritatea pentru pontaj și creditul comercial personal.
7. Reatribuirea nu modifică totalul fizic al magazinului/companiei.
8. Programul, pontajul, atribuirea și grila trebuie să poată fi legate de aceeași
   revision/generation acceptată.
9. O lună `CLOSED` respinge mutațiile business.
10. Reopen este controlat, motivat și auditat; istoricul de close nu se șterge.
11. Orice input financiar obligatoriu lipsă/stale produce blocker la close,
    conform `ClosePolicy`/rule pack activ.
12. Auditul mutațiilor relevante este append-only și păstrează actor/before/after.

## 7. Calendar, pontaj și atribuire

Calendarul este sursa unică pentru ziua persoanei. Editarea folosește revision/CAS
pentru a evita lost updates.

Pontajul este o proiecție; nu are editor independent. Pentru Mobiup, politica
exactă este în `docs/MOBIUP_RULE_PACK.md`.

Vânzarea fizică este păstrată separat de creditul personal:

```text
sales_store_day = adevăr fizic
calendar        = cine a lucrat
sales_person_day projection = credit comercial derivat
```

Astfel mutarea unui agent poate schimba creditul personal fără să dubleze sau să
modifice vânzarea magazinului.

## 8. Grid engine și financial close

Grid engine-ul rămâne separat de transport și folosește:

- canonical input payload;
- `Decimal`;
- rule pack versionat;
- input/output hashes;
- anomalii explicite;
- snapshoturi de salariu/tichete/Flip/E-pay/target/vânzări.

Preview-ul poate exista cu anomalii. **Close-ul final nu poate transforma lipsa
unui input financiar cerut într-un rezultat aparent valid.**

Candidate-ul trebuie să introducă o clasificare explicită:

```text
Anomaly / precondition
  |- informational
  |- warning
  `- blocking_for_close
```

E-pay fresh/complet este blocking atunci când rule pack-ul îl include în calcul.

## 9. Google Sheets și XLSX

Google este un adaptor asincron, nu o dependență de read-path-ul UI.

Reguli:
- binding stabil per magazin;
- ultima proiecție bună rămâne disponibilă la failure;
- numai celulele E-pay permise sunt editabile;
- readback-ul validează exact persoanele/categoriile așteptate;
- projection metadata include revision/generation/rule-pack/timestamps;
- quota/network failures sunt retryable bounded;
- business/structural failures sunt terminale până la remediere.

XLSX este un format de import/export/reconciliere. Fișierele exportate sunt
deterministe, scoped și fără linkuri externe către Retail/Google.

## 10. Frontend

Frontendul standalone rămâne React/TypeScript/Vite în acest program.

Direcția vizuală este compatibilă cu UniHub Retail: shell luminos, densitate
operațională, tabs compacte, tabele/carduri mici și accent lavender. Nu este
necesară migrarea la stack-ul exact Retail înainte de integrarea finală.

Obligații candidate:
- toate acțiunile vizibile au endpoint real;
- capabilities controlează UI-ul, backend-ul rămânând autoritativ;
- requesturile/subsistemele au stări independente;
- Google/export error nu prăbușește calendarul/grid-ul deja disponibil;
- loading/empty/stale/403/409/error sunt stări de produs;
- desktop/tablet/mobile + keyboard accessibility;
- testele de componente nu înlocuiesc browser validation când aceasta este
  necesară.

## 11. Contractul viitor Retail

Contractul complet este în `docs/RETAIL_INTEGRATION_CONTRACT.md`.

Minimum input package:
- schema version;
- tenant/timezone/generation;
- stores/hierarchy;
- people/home-store/effective activity;
- manager/effective scopes;
- sales store/day;
- targets;
- incentive/alte inputuri externe cerute de rule pack.

Minimum identity package:
- user/subject;
- tenant;
- roles/capabilities;
- effective resource scope.

La integrare, fixture adapters sunt înlocuite cu Retail adapters. Domain/services
nu trebuie rescrise.

## 12. Current vs target

Arhitectura de mai sus este implementată pentru candidatul standalone server-test;
statusul gate-ului și SHA-ul instalabil rămân autoritative exclusiv în issue #4.

Mecanismele care au fost harden-uite în program și nu mai sunt TODO-uri:
- `dev_headers` este strict environment-gated, iar configurația production
  fail-closed nu acceptă development identity;
- capability + tenant + effective resource scope sunt backend-enforced, cu teste
  negative cross-tenant/cross-manager;
- fixture ingest nu este montat în production;
- `ClosePolicy` verifică inputurile financiare obligatorii și serializează
  mutațiile relevante pe Month;
- workerul are lease committed, bounded retry/backoff, stale-RUNNING recovery,
  idempotency și supersession/revision binding;
- Google fake/live și XLSX au contracte fail-closed, readback/protection,
  revision/generation pinning și publicare deterministă/atomică;
- CI acoperă backend strict, PostgreSQL/migrații, frontend și browser E2E, iar
  runtime-ul expune health/readiness/metrics/logging structurat.

Limite deliberate care rămân în afara gate-ului standalone:
- identitatea production reală va fi furnizată de adaptorul Retail/host;
- adaptorul Retail real și montarea în shell-ul Retail sunt fază separată;
- live Google canary, deployment/production activation și măsurătorile pe hostul
  real necesită autorizare/executare separată.

## 13. Performance și operare

Ținte orientative pentru candidate:
- overview reprezentativ p95 <500 ms local/pilot;
- save calendar DB path <500 ms în condiții normale;
- fără N+1 nebounded în ecranele principale;
- Google/export exclusiv asincron;
- fresh PostgreSQL bootstrap determinist;
- logs/metrics cu correlation IDs și fără PII/payroll leakage;
- startup fail-closed pentru configurație prod nesigură.

Criteriile finale și scorarea sunt în `docs/QUALITY_GATES.md`.
