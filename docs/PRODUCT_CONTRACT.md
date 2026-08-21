# UniHub Grile — contract de produs

Status: **canonical product contract pentru programul Standalone Plugin Candidate**  
Plan: GitHub issue #3  
Tracker: GitHub issue #4

Acest document definește comportamentul produsului. Nu definește statusul
implementării; statusul exact este numai în trackerul #4.

## 1. Scop

UniHub Grile trebuie să permită managerilor să controleze programul, pontajul,
suplimentările, atribuirea vânzărilor, excepțiile și grilele salariale într-un
mod determinist și auditabil.

Aplicația este dezvoltată standalone acum și va fi integrată ulterior în UniHub
Retail. Integrarea Retail nu este o condiție pentru maturizarea funcțională a
pluginului și nu trebuie să blocheze dezvoltarea curentă.

## 2. Roluri și capabilities

Rolurile sunt convenții de produs; backend-ul trebuie să decidă accesul prin
capabilities + resource scope, nu numai prin eticheta rolului.

### Administrator

Poate, în limita tenantului:
- vede toate magazinele, persoanele, managerii și lunile;
- gestionează programul;
- vede și rezolvă excepții;
- citește/validează E-pay;
- lansează sync/export/import;
- vede joburi și audit;
- închide luna;
- redeschide luna cu motiv obligatoriu;
- gestionează configurații administrative permise de produs.

### Manager zonal / TL

Poate numai în `effective resource scope`:
- vede magazinele și persoanele permise;
- construiește/modifică programul;
- clasifică `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER`, `OFF`, `LEAVE`;
- vede pontajul/grila/excepțiile relevante;
- folosește import/export/sync numai dacă are capability explicit;
- nu poate accesa un alt magazin doar cunoscând `store_id`.

Dreptul de `close` rămâne administrativ până când o politică viitoare spune
explicit altceva.

### Agent

În candidate-ul curent:
- nu are editor în aplicația Grile;
- primește proiecția Google Sheet a magazinului;
- vede numai informația destinată magazinului/persoanelor respective;
- poate modifica exclusiv cantitățile E-pay permise de contractul Sheet.

Un viitor acces direct al agentului în Retail/Grile este în afara acestui
program.

## 3. Scope și autorizare

Orice operație trebuie să verifice două dimensiuni:

```text
tenant authorization
        +
resource authorization (store/person/month/export/job)
```

Resource scope este effective-dated unde modelul business o cere.

Reguli:
- admin vede tenant-wide numai dacă principalul are capability corespunzător;
- managerul vede numai aria curentă/effective-dated;
- read și write folosesc aceeași sursă centrală de scope;
- export/download/status/job endpoints respectă același scope ca ecranele;
- frontend-ul poate ascunde o acțiune indisponibilă, dar backend-ul rămâne
  autoritatea de securitate.

## 4. Calendar și program

Granularitatea business este ziua întreagă.

### Stări lucrate

- `NORMAL` — persoana lucrează în home store;
- `EXTRA_HOME` — persoana lucrează suplimentar în home store;
- `EXTRA_OTHER` — persoana lucrează suplimentar în alt store.

### Stări fără lucru

- `OFF` — liber;
- `LEAVE` — concediu.

Invariante:
- max. un agent lucrător per store/date;
- max. un store lucrat per person/date;
- `EXTRA_HOME` cere `store == home_store`;
- `EXTRA_OTHER` cere `store != home_store`;
- `OFF/LEAVE` nu ocupă acoperirea magazinului;
- draftul poate conține goluri vizibile; close nu poate.

Managerul editează direct ziua. Nu există un wizard separat „schimb de tură”.

Orice write folosește revision/CAS sau un mecanism echivalent care previne lost
update. Conflictul stale trebuie să păstreze contextul utilizatorului și să ofere
o cale clară de refresh/retry.

## 5. Pontaj

Pontajul este **derivat din calendar** și nu are o a doua autoritate.

Pentru Mobiup, orele și layout-ul sunt definite de
`docs/MOBIUP_RULE_PACK.md`.

O modificare a calendarului trebuie să actualizeze proiecția pontajului în
aceeași revizie business sau într-o secvență tranzacțională echivalentă care nu
expune rezultate incompatibile.

Nu există editare manuală a pontajului în Google Sheet.

## 6. Atribuirea vânzărilor

Sursa fizică este vânzarea magazinului/zi din connector.

```text
sales_store_day = adevăr fizic
calendar        = persoana care a lucrat
sales_person_day = credit derivat
```

Reguli:
- întreaga vânzare a magazinului/zi este creditată agentului planificat;
- mutarea agentului schimbă creditul personal, nu totalul magazinului;
- `EXTRA_OTHER` creditează persoana pentru store-ul gazdă;
- `EXTRA_HOME` nu dublează vânzarea;
- ziua cu zero/multiple assignments rămâne anomalie explicită;
- missing sale nu este convertit în „vânzare zero validă” fără marcaj/anomalie.

## 7. Rule pack și calcul salarial

Mobiup folosește un rule pack versionat definit în
`docs/MOBIUP_RULE_PACK.md`.

Motorul trebuie să păstreze:
- canonical inputs;
- rule-pack version/hash;
- revision/generation;
- componente calculate separat;
- input/output hashes;
- anomalii explicite;
- `Decimal` și politica de rotunjire specificată.

Regulile Mobiup nu se distribuie ca `if`/constante prin API și repositories.

Preview-ul poate fi calculat cu warning-uri. Rezultatul final de close trebuie
să respecte politica de blocare din secțiunea următoare.

## 8. E-pay

Categorii inițiale Mobiup per agent:
- `<50 lei`;
- `>=50 lei`.

Valori acceptate: întreg `0..10`.

Pentru un magazin standard cu doi agenți rezultă patru inputuri așteptate.

Reguli:
- readback-ul validează exact persoanele/categoriile așteptate;
- blank/text/fraction/negative/>10 este invalid;
- invalidul este auditat și nu șterge ultima valoare bună;
- freshness are timestamp/prag explicit;
- după close nu se acceptă ingest business fără reopen;
- dacă rule pack-ul folosește E-pay, lipsa/freshness invalidă este blocker la
  close.

## 9. Anomalii și excepții

Orice anomalie relevantă are:
- cod stabil;
- store/person/date unde este cazul;
- mesaj și context;
- severitate;
- clasificare `informational`, `warning` sau `blocking_for_close`;
- acțiune/recomandare de remediere unde este posibil.

Exemple:
- store/day neacoperit;
- persoană în două store-uri;
- `EXTRA_HOME/OTHER` invalid;
- vânzare lipsă;
- target zero/lipsă;
- sales-day divisor lipsă;
- salary master lipsă;
- E-pay lipsă/stale/invalid;
- generații/revizii incompatibile;
- Google projection stale/error.

Nu toate warning-urile trebuie să blocheze preview-ul. Orice condiție care poate
face salariul final nedeterminat sau nevalidat trebuie să blocheze close-ul.

## 10. Luna, close și reopen

Stări business:
- `DRAFT` — program incomplet permis; calculele sunt preview;
- `OPEN` — mutații permise conform capabilities;
- `CLOSED` — program, E-pay, atribuire și rezultatul final sunt imuabile;
- `REOPENED` — corecție auditată înainte de un nou close.

Close folosește o politică versionată și verifică cel puțin:
- acoperire validă a fiecărui store/day obligatoriu;
- nicio persoană în două store-uri/zi;
- vânzări/targete necesare prezente și reconciliabile;
- E-pay fresh/complet dacă este folosit;
- salary/master inputs necesare prezente;
- calculele complete și pe revision/generation compatibilă;
- anomalii `blocking_for_close == 0`;
- concurrency/revision corectă la momentul finalizării.

Google Sheet stale poate fi vizibil ca problemă operațională fără să altereze
engine-ul; dacă însă politica cere un readback/projection canary pentru close,
acea precondiție devine blocker explicit, nu warning ascuns.

Close persistă snapshot/digest/audit. Reopen:
- admin-only;
- motiv obligatoriu;
- creează audit nou;
- nu șterge close-ul anterior;
- increment/revision coerent;
- permite apoi recalcul și un nou close.

## 11. Audit

Business mutations relevante trebuie să producă audit append-only.

Minimum pentru program/calendar:
- actor/principal;
- tenant;
- resource;
- before;
- after;
- revision before/after;
- source (`UI`, `XLSX`, system etc.);
- request/correlation id;
- timestamp.

Aceeași disciplină se aplică E-pay admin actions, import apply, close și reopen.

## 12. Google Sheets

Google Sheet este o **proiecție regenerabilă** și o suprafață de input E-pay
restrânsă.

Contract:
- binding stabil store↔workbook;
- `Grila` + `Pontaj` conform specului UX;
- numai inputurile E-pay desemnate editabile;
- fără date din alte store-uri;
- fără business logic financiară autoritativă în formule editabile;
- projection metadata: generation/revision/rule-pack/timestamp;
- last-good rămâne disponibil la failure;
- sync/readback rulează asincron în worker.

## 13. Import program XLSX

Flux:
1. download template pentru lună/scope;
2. identificatori tehnici/version manifest;
3. upload;
4. preview fără writes;
5. listă diff + blockers;
6. apply explicit și atomic;
7. revision/CAS protejează împotriva stale;
8. calendar/pontaj/attribution/grid sunt regenerate;
9. external projections se lansează async.

Fișierul nu creează liber stores/people și nu folosește matching după nume ca
identitate.

## 14. Export XLSX

Tipuri:
- per-store: `Grila` + `Pontaj`;
- bulk scoped: ZIP + manifest/checksums;
- pontaj-only scoped.

Reguli:
- reproducibil pentru revision/generation dată;
- fără referințe externe la Retail/Google;
- fără date în afara resource scope;
- verificare prin parsing workbook, nu doar „file exists”;
- export mare este job asincron;
- artefactele au retention/cleanup bounded.

## 15. Frontend

Ecranele primare candidate:
- Hub;
- Program & Calendar;
- Magazin;
- Agent/detail unde aduce valoare operațională;
- Excepții;
- Management/close/reopen;
- jobs/sync/export status unde este necesar.

Reguli UX:
- design compatibil vizual cu UniHub Retail, dar app standalone în dezvoltare;
- toate datele operaționale vin din API real;
- nicio acțiune fake în production path;
- capability-aware controls;
- subsystem errors independente;
- stări loading/empty/stale/retry/403/409/error;
- desktop first, tablet/mobile utilizabil;
- keyboard accessibility pentru acțiunile principale.

## 16. Integrarea Retail — contract, nu implementare curentă

În acest program Retail rămâne nemodificat.

Grile pregătește intern contracte pentru:
- identity/capabilities;
- tenant/timezone;
- stores/hierarchy;
- people/home-store/activity;
- manager/effective scopes;
- sales store/day;
- targets;
- incentive/alte inputuri cerute de rule pack.

Detalii: `docs/RETAIL_INTEGRATION_CONTRACT.md`.

## 17. Non-goals până la server-test candidate

- modificarea UniHub Retail;
- autentificare Retail end-to-end;
- mutarea Grile în runtime/deploy Retail;
- agent self-service complex;
- schimb de tură cu workflow de aprobare;
- pontaj biometric;
- ture multiple într-o zi;
- editare bidirecțională generală în Google;
- calcul salarial din cod POS ca identitate;
- formule financiare autoritative în Sheet.

## 18. Definition of done al produsului standalone candidate

Acest contract este suficient de implementat pentru test pe server numai când
`SERVER-TEST-READY` din issue #4 este bifat conform `docs/QUALITY_GATES.md`.
