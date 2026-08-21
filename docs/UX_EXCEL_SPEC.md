# UniHub Grile — UX, Google Sheets și XLSX

Status: canonical UX/integration-output specification pentru programul din issue #3.

Acest document definește experiența țintă. Statusul implementării este în issue
#4; exemplele vizuale sau prototipurile nu sunt dovadă că o funcție există.

## 1. Principii UX

Managerul trebuie să poată răspunde rapid la:

1. cine lucrează și unde;
2. unde există probleme;
3. ce date intră în grilă;
4. ce blochează închiderea lunii;
5. ce job/sync/export a eșuat și de ce.

Reguli:
- luna și scope-ul curent sunt vizibile permanent;
- densitate operațională, fără hero/carduri oversized;
- editarea calendarului este directă;
- text + culoare pentru status, nu doar culoare;
- Google/export nu blochează navigarea;
- fiecare subsystem are loading/error/stale separat;
- acțiunile vizibile trebuie să aibă endpoint real;
- capability-aware UI, backend autoritativ;
- desktop este principal, tablet/mobile rămân utile;
- keyboard focus și controale accesibile.

## 2. Limbaj vizual

Direcția aprobată este apropiată de UniHub Retail:
- shell luminos;
- sidebar compact alb;
- topbar subțire;
- workspace gri foarte deschis;
- carduri albe cu radius mic și shadow discret;
- accent lavender/purple;
- tabs/subtabs compacte;
- fonturi mici, dar lizibile;
- tabele dense și drill-down rapid;
- fără dark command-center, fără carduri SaaS gigantice.

Nu copiem automat meniuri Retail fără relevanță pentru Grile. De exemplu,
`Vizite` nu aparține navigației Grile doar pentru că există în Retail.

## 3. Navigație standalone candidate

Navigația principală:
- **Hub**;
- **Program & Calendar**;
- **Excepții**;
- **Management**.

Drill-down-uri:
- Magazin;
- Agent/detail unde este util;
- jobs/sync/export status.

La integrarea finală, shell-ul poate fi înlocuit/montat în Retail fără schimbarea
contractelor ecranelor.

## 4. Hub

Scop: imagine centralizată a lunii și a excepțiilor.

Conținut minim:
- luna/state/revision;
- magazine în scope;
- persoane în scope;
- procent completare calendar;
- blockers/warnings;
- E-pay freshness;
- sales/target anomalies;
- Google projection freshness;
- job failures;
- manager/store grouping și drill-down.

Overview-ul nu trebuie să facă request separat per store pentru agregări. Datele
sunt server-side aggregate/read models sau batch eficiente.

Un Google failure nu transformă automat întregul Hub în error dacă datele
calendar/grid sunt disponibile.

## 5. Program & Calendar

Perspectivele recomandate:

### Pe persoane

Rând = persoană; coloană = zi; celulă:
- store/`NORMAL`;
- `EXTRA_HOME`;
- `EXTRA_OTHER`;
- `OFF`;
- `LEAVE`.

### Pe magazine

Rând = store; coloană = zi; celulă = persoana lucrătoare + clasificare.

Este permis să se livreze întâi perspectiva existentă dacă acoperă workflow-ul;
a doua perspectivă se adaugă numai dacă îmbunătățește operarea, nu pentru
simetrie artificială.

### Editare celulă

Click/select cell → editor compact:
- persoană/date context;
- status;
- working kind;
- store permis;
- save.

Reguli:
- dropdownurile sunt constrained de scope și reguli;
- stale revision `409` nu șterge contextul/editarea fără explicație;
- conflictele sunt afișate concret;
- după save UI reîncarcă revision/datele afectate;
- nu există wizard separat de schimb de tură.

## 6. Magazin

Pagina magazinului reunește:
- identitate, firmă/manager/scope;
- state/revision/freshness;
- agenți/home assignments;
- calendar lunar;
- pontaj;
- sales/attribution;
- grid components și anomalies;
- E-pay/readback freshness;
- Google projection status;
- export status.

Acțiuni posibile numai dacă capability permite:
- editare program;
- sync Sheet;
- export XLSX;
- readback E-pay;
- drill-down excepții.

Pagina trebuie să rămână parțial utilizabilă dacă un subsystem extern cade.

## 7. Agent/detail

Dacă este expus:
- home store și manager;
- calendar personal;
- normal/off/leave/extra-home/extra-other;
- pontaj;
- credit sales derivat;
- grid components/anomalies;
- E-pay;
- fără editor paralel care poate contrazice calendarul principal.

## 8. Excepții

Fiecare rând/card are:
- cod;
- severitate;
- blocker/warning/info;
- store/person/date;
- impact;
- sursa problemei;
- acțiune/drill-down;
- status rezolvare dacă există.

Categorii:
- coverage;
- duplicate person assignment;
- invalid extra kind;
- missing sale/target/divisor;
- salary master missing;
- E-pay stale/invalid/missing;
- revision/generation mismatch;
- Sheet stale/structural error;
- worker/job failure.

## 9. Management / close

Ecranul afișează:
- month state;
- expected revision;
- checklist grupat;
- hard blockers separat de warnings;
- E-pay freshness/readback;
- calculation/generation consistency;
- audit timeline;
- explicit confirm close;
- reopen separat, admin-only, reason required.

Butonul `Închide luna` este imposibil/disabled cât timp există blocker. Backend-ul
revalidează în tranzacția de close; UI-ul nu este autoritate.

## 10. Jobs / sync / export states

Pentru operațiile asincrone se folosesc stări coerente:
- queued;
- running;
- retrying;
- done;
- failed;
- superseded/cancelled unde este implementat.

UI arată attempts, last run, last success, last error și retry/recovery action
dacă este permisă.

Refresh-ul browserului nu pierde posibilitatea de a urmări un job persistent.

## 11. Stări standard frontend

Fiecare query/section trebuie să poată reda:
- loading;
- empty;
- ready;
- stale;
- forbidden `403`;
- revision conflict `409`;
- retryable external error;
- terminal business error.

Nu folosim un singur `Promise.all` care transformă o eroare periferică într-un
blank screen complet când datele principale sunt disponibile.

## 12. Import program XLSX

Workbook generat:

### `Instrucțiuni`
- lună;
- scope;
- base revision;
- legendă;
- avertisment privind ID-urile/structura.

### Tab(uri) de program
- ID tehnic stabil;
- nume afișat;
- home store;
- zile `1..31`;
- dropdownuri limitate la valori valide/scoped;
- weekend highlight;
- fără creare liberă de stores/people.

### `Manifest`
- schema version;
- tenant/month;
- principal/scope token sau hash adecvat;
- base revision;
- catalog checksums;
- generated_at/expiry unde este cazul;
- fără credentials sau salarii inutile.

Flux:
1. download;
2. modificare offline;
3. upload;
4. preview read-only;
5. diff + blockers;
6. apply explicit;
7. atomic write/CAS;
8. regenerate projections;
9. enqueue external sync.

## 13. Export XLSX

### Per magazin
Workbook cu:
- `Grila`;
- `Pontaj`.

`Grila` păstrează layout-ul Mobiup acceptat, componentele și metadata necesară.
`Pontaj` respectă contractul `C:AG`, blocurile de 3 rânduri și totalurile `AH`
definite în rule pack.

### Bulk
- scope filters;
- ZIP;
- un workbook per store;
- manifest cu file checksum, revision/generation/rule pack;
- deterministic naming.

### Pontaj-only
- aceleași reguli de scope;
- workbook minim cu `Pontaj`.

Verificarea automată deschide/parcurge workbookul și inspectează sheet names,
celule, formule permise, valori și external links.

## 14. Google Sheets

Google Sheet este output controlat plus input E-pay limitat.

### `Grila`
- rezultate/calcul;
- calendar/context;
- două categorii E-pay per agent;
- numai inputurile E-pay desemnate editabile.

### `Pontaj`
- read-only projection din calendar;
- fără editări manuale păstrate ca autoritate.

### Metadata operațională
Trebuie să poată fi corelată cu:
- store;
- month;
- revision;
- source generation;
- rule-pack version;
- projected_at;
- last-success/last-error.

Provider failure nu distruge last-good projection.

## 15. E-pay UI/readback

Manager/admin trebuie să vadă:
- expected inputs;
- last valid value;
- last observed time;
- freshness status;
- invalid raw values unde este necesar pentru audit;
- exact ce intră în calcul înainte de close.

Readback invalid nu șterge last-good și nu poate trece close-ul dacă inputul este
obligatoriu/freshness insuficient.

## 16. Responsive și accessibility

Desktop:
- tabele/matrice dense;
- sticky labels/header unde ajută;
- scroll vizibil.

Tablet:
- layout 1 coloană unde este nevoie;
- calendar scroll orizontal controlat.

Mobile:
- overview, excepții și corecții punctuale;
- nu comprimăm 31 coloane într-un layout inutilizabil;
- acțiuni critice rămân accesibile.

Accessibility:
- focus vizibil;
- labels;
- semantic buttons/inputs;
- keyboard navigation;
- statusul nu depinde numai de culoare.

## 17. Performance targets

Ținte candidate orientative:
- overview p95 <500 ms pe fixture reprezentativ;
- calendar load <1 s la volum țintă local/pilot;
- save DB <500 ms normal;
- un singur filter change nu lansează N requesturi per store;
- Google/export async;
- bulk export nu ține request HTTP deschis pe durata generării.

Măsurătorile și gate-ul final sunt în `docs/QUALITY_GATES.md`.

## 18. Validare vizuală

Pentru gate-ul final sunt necesare, când există browser runner:
- 1440px desktop;
- tablet;
- mobile;
- 31-day calendar;
- realistic 75+ store fixture;
- keyboard/focus;
- loading/403/409/error/stale;
- failed Google subsystem cu restul paginii funcțional;
- XLSX/Sheet comparison cu contractele acceptate.

Component tests sau build success nu sunt echivalente cu această validare.
