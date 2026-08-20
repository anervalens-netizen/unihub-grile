# Specificație UX, import și export

## 1. Principii UX

- managerul trebuie să poată răspunde rapid la trei întrebări: „cine lucrează?”,
  „unde există probleme?” și „ce intră în grilă?”;
- luna și aria curentă sunt vizibile permanent;
- editarea calendarului este directă, cu validare imediată;
- stările importante folosesc text + culoare, nu doar culoare;
- Google sync și calculele nu blochează navigarea;
- acțiunile bulk au preview și rezumat înainte de apply;
- desktop este suprafața principală; tableta rămâne utilizabilă; telefonul oferă
  overview și corecții punctuale, nu matricea completă.

## 2. Navigație propusă

### Overview

Header: lună, firmă, manager, magazin, status lună și acțiuni Import/Export.

Carduri KPI:

- magazine acoperite / total;
- zile neacoperite;
- conflicte agent/zi;
- zile suplimentare acasă / în alt magazin;
- vânzări neatribuite;
- E-pay invalid/necitit;
- Google Sheets sincronizate/stale/error.

Conținut:

- tabel pe manager cu progres, excepții, ultimul sync și drill-down;
- listă „Necesită atenție” ordonată după severitate;
- fără agregări financiare obținute prin request separat per magazin.

### Program

Două perspective asupra aceleiași surse:

1. **Pe magazine** — rânduri magazine, coloane zile, în celulă agentul alocat și
   badge `Normal`, `Extra aici` sau `Extra altă locație`.
2. **Pe agenți** — rânduri agenți, coloane zile, în celulă magazinul sau
   `Liber`/`Concediu`.

Interacțiunea principală: click pe celulă, alegere agent și clasificare. Nu există
wizard de schimb de tură. Salvarea unei modificări este atomică și afișează
conflictele rezultate. Sunt utile: copiere săptămână, copiere lună anterioară și
completare model alternant, dar numai după funcționarea editorului de bază.

### Magazin

- identitate, firmă, manager, agenții de bază, stare Sheet;
- calendarul lunar al magazinului;
- target, realizat, forecast și vânzare pe zile;
- atribuirea agentului pentru fiecare zi;
- zile suplimentare primite și efectul lor;
- E-pay pe agent și momentul ultimei citiri;
- previzualizare grilă și pontaj;
- acțiuni: sync Sheet, export XLSX, deschide Sheet.

### Agent

- magazin de bază și manager;
- calendar personal;
- zile normale, libere, concediu, extra acasă și extra în alte magazine;
- credit de vânzare zilnic/lunar;
- componentele grilei și E-pay;
- fără editor separat care poate contrazice Programul.

### Excepții

- magazin fără agent;
- agent în două magazine;
- atribuire invalidă home/other;
- vânzare fără calendar;
- E-pay invalid sau necitit;
- sursă stale/incompletă;
- Sheet stale/structural invalid/sync error.

Fiecare excepție are context, impact, acțiune directă și blocant/non-blocant
pentru close.

### Închidere lună

- checklist complet;
- exacta revizie/generație;
- preview totaluri și exporturi;
- confirmare explicită;
- rezultat imuabil și linkuri către exporturi;
- reopen separat, admin-only, cu motiv obligatoriu.

## 3. Model import program XLSX

Workbookul generat de aplicație conține:

### `Instrucțiuni`

- luna, aria, revision și momentul exportului;
- legendă scurtă;
- mesaj că numele/codurile tehnice nu trebuie modificate.

### Câte un tab per manager

- primele coloane: cod agent stabil, nume, firmă, magazin de bază;
- coloanele următoare: zilele `1..31` ale lunii;
- fiecare celulă are dropdown cu valori permise:
  - `LIBER`;
  - `CONCEDIU`;
  - `NORMAL - <magazin de bază>`;
  - `SUPLIMENTAR ACASĂ - <magazin de bază>`;
  - `SUPLIMENTAR - <magazin permis>`.
- weekendurile sunt evidențiate;
- foile/listările tehnice cu ID-uri sunt ascunse și protejate.

Acest format este centrat pe persoană și permite managerului să completeze luna
natural. La preview, aplicația construiește acoperirea pe magazine și raportează
orice zi cu zero/doi agenți. Nu se bazează pe numele afișat; fiecare rând conține
un identificator intern semnat/validat în manifest.

### `Manifest`

- schema importului;
- tenant, lună, manager scope, base revision;
- checksum al listelor de persoane și magazine;
- fără credentiale sau date salariale.

## 4. Flux import

1. `Descarcă model` pentru lună și filtre.
2. Upload cu limită de mărime și scanare structură.
3. Preview fără scrieri:
   - rânduri/celule schimbate;
   - zile care devin extra;
   - conflicte/goluri;
   - identificatori necunoscuți;
   - revision stale.
4. Apply numai dacă toate erorile blocante sunt zero.
5. O singură tranzacție actualizează calendarul și revizia.
6. Pontajul, atribuirea și grila sunt regenerate.
7. Workerul sincronizează Sheets; eșecul Google nu revocă programul valid, ci
   păstrează ultima proiecție și afișează eroarea.

## 5. Exporturi

### XLSX magazin

- tab `Grila`: păstrează structura vizuală V2 acceptată — rezumat magazin,
  carduri separate pentru cei doi agenți, salariu și proiecție, calendar,
  concedii și suplimentare;
- tab `Pontaj`: păstrează structura din captura furnizată;
- valori și formate monetare românești;
- fără referințe externe și fără date din alte magazine.

### Bulk

- filtre: lună, firmă, manager, magazin;
- ZIP cu un fișier per magazin și manifest JSON/XLSX;
- nume deterministe, fără coliziuni între firme;
- progres și rezultat disponibile ca job, fără ținerea requestului deschis.

### Pontaj-only

- aceleași filtre;
- fișiere cu un singur tab `Pontaj`;
- valori stabile și totaluri verificabile.

## 6. Cerințe de performanță verificabile

- overview lunar: p95 backend sub 500 ms pe setul pilot și sub 1 s la volumul
  țintă, fără Google I/O;
- schimbare filtru: un request agregat, nu N requesturi per magazin;
- deschidere calendar lunar: sub 1 s la volumul țintă, cu virtualizare;
- salvare celulă program: confirmare DB sub 500 ms; Google sync asincron;
- joburile au stare și progres; refreshul paginii nu pierde urmărirea;
- exportul bulk nu încarcă integral fișierul în memoria procesului web.

## 7. Validare vizuală

Înainte de GO pentru Stage 4/5 se verifică în browser, pe date fixture:

- overview la 1440 px, tabletă și telefon;
- matrice lunară cu 31 zile și minimum 75 magazine;
- contrast, focus keyboard, tooltips și stări de eroare;
- grila și pontajul randate din XLSX/Google canary comparativ cu capturile;
- celulele E-pay sunt singurele modificabile în Sheet.

