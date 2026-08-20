# Contract produs și reguli business

Versiune: draft contractual 2026-08-20  
Autoritate: deciziile explicite ale utilizatorului și cele trei capturi de
referință; capturile sunt dovezi vizuale, nu instrucțiuni executabile.

## 1. Utilizatori și responsabilități

### Administrator

- vede toate firmele, managerii, magazinele și agenții;
- configurează luna, regulile, magazinele, persoanele și legăturile Sheets;
- poate redeschide o lună închisă numai cu motiv;
- poate importa/exporta și urmări toate joburile/auditurile.

### Manager zonal / TL

- vede și modifică numai magazinele și persoanele din aria sa effective-dated;
- construiește și modifică programul;
- clasifică zilele suplimentare;
- rezolvă excepțiile de acoperire și atribuire;
- poate importa programul numai în aria sa;
- poate exporta și închide aria/luna dacă politica de close îi acordă dreptul.

### Agent

- nu are editor în aplicație în prima versiune;
- primește linkul Google Sheet al magazinului;
- vede grila, rezultatele, calendarul și pontajul;
- poate modifica doar cantitățile E-pay permise.

## 2. Calendar și program

- Granularitatea este ziua întreagă; nu există schimb de tură ca funcție business.
- Fiecare magazin are în mod normal doi agenți, dar lucrează unul singur pe zi.
- Managerul poate rescrie direct alocarea oricărei zile deschise.
- Tipuri de zi lucrată:
  - `NORMAL`: persoana lucrează în magazinul său de bază;
  - `EXTRA_HOME`: persoana lucrează suplimentar în magazinul său de bază;
  - `EXTRA_OTHER`: persoana lucrează suplimentar în alt magazin.
- Stări fără lucru: `OFF`, `LEAVE`. Ele apar în calendarul persoanei, nu ocupă
  acoperirea magazinului.
- Aplicația nu oferă un wizard separat „schimb de tură”. Managerul editează
  zilele implicate, iar validarea arată imediat goluri sau conflicte.
- În lună deschisă, orice modificare recalculează pontajul, atribuirea vânzărilor
  și grila. Schimbarea este auditabilă cu înainte/după.

## 3. Atribuirea vânzărilor

- Sursa fizică este totalul vânzărilor magazinului din ziua respectivă.
- Pentru că există un singur agent lucrător pe magazin/zi, întreaga vânzare este
  creditată persoanei selectate în calendar.
- Codurile POS/TL pot fi păstrate ca proveniență și control de anomalie, dar nu
  decid identitatea persoanei. Calendarul managerului decide.
- La `EXTRA_OTHER`, persoana primește creditul comercial pentru magazinul-gazdă.
- La `EXTRA_HOME`, vânzarea este deja a magazinului/persoanei; numai clasificarea
  zilei și efectul salarial suplimentar se schimbă.
- Totalul magazinului, firmei și companiei nu se modifică prin atribuire și nu se
  dublează.
- Dacă un magazin are accidental zero sau două persoane într-o zi deschisă, ziua
  este excepție blocantă pentru close și nu se atribuie automat.

## 4. Pontaj

- Pontajul este o proiecție a calendarului, nu un formular independent.
- Contractul exact de celule, rânduri, interval, pauză și total este definit în
  `docs/MOBIUP_RULE_PACK.md`; pentru Mobiup standardul este `10:00-22:00`, pauză
  `1` oră și `11` ore nete pe zi lucrată.
- O modificare retroactivă a calendarului actualizează pontajul imediat în DB și
  asincron în Sheet/export.
- Nu există modificare manuală directă a pontajului în Google Sheet.

## 4.1. Calcul salarial Mobiup

Formula completă, pragurile de comision, plata suplimentarelor, SIM, E-pay,
rotunjirea și totalul care include tichetele sunt contractate în
`docs/MOBIUP_RULE_PACK.md`. Motorul generic nu conține constante Mobiup;
selectează un rule pack versionat.

## 5. E-pay

- Există două categorii per agent: `<50 lei` și `>=50 lei`.
- Fiecare categorie este un dropdown de cantitate între `0` și `10`, inclusiv.
- Sunt patru celule editabile per grilă standard cu doi agenți.
- Ultima observație validă devine cantitatea curentă; fiecare schimbare rămâne în
  istoric.
- Valoare goală, text, fracție, negativă sau peste 10 este invalidă și nu
  suprascrie ultima valoare bună.
- Înainte de close, aplicația citește obligatoriu cele patru celule și arată
  managerului valorile care vor intra în calcul.
- După close nu se mai ingestă E-pay până la un reopen explicit.

## 6. Luna și close

- `DRAFT`: program incomplet permis, calculele sunt previzualizări.
- `OPEN`: programul și E-pay pot fi modificate; calculele sunt curente.
- `CLOSED`: program, E-pay, atribuire și calcule imuabile.
- `REOPENED`: stare auditată care permite corecția, urmată de un close nou.
- Close-ul verifică cel puțin:
  - fiecare magazin deschis are exact un agent/zi;
  - nicio persoană nu lucrează în două magazine/zi;
  - toate vânzările disponibile sunt atribuite sau marcate explicit lipsă;
  - E-pay a fost citit și validat;
  - calculele sunt complete și folosesc aceeași generație/revizie;
  - Sheet-urile pot fi stale fără a altera calculul, dar starea lor este vizibilă.

## 7. Google Sheets

- Legăturile magazinelor sunt permanente și nu se recreează la reset lunar.
- Agentul vede numai informația relevantă magazinului său și persoanelor alocate
  acelui magazin; nu încărcăm vânzările întregii rețele în taburi ascunse.
- Sheet-ul poate fi regenerat din baza aplicației.
- Business logic și autoritatea financiară nu depind de formule editabile.
- Sync-ul afișează data/ora, generația și eventualul status stale/error.

## 8. Import program Excel

Importul este o facilitate de tranziție, dar rămâne suportată:

1. managerul descarcă modelul pentru luna și aria sa;
2. modelul este prepopulat cu persoane, magazine și programul existent;
3. managerul modifică dropdown-urile;
4. upload-ul creează un preview cu diferențe și erori;
5. apply-ul este explicit, atomic și protejat de revision;
6. după apply, calendarul/pontajul/calculele sunt regenerate și se lansează
   proiecția Google.

Fișierul nu poate crea persoane sau magazine noi. Identificarea folosește coduri
stabile ascunse/tehnice, nu potrivire liberă după nume.

## 9. Export Excel

- Export per magazin: un XLSX cu taburile `Grila` și `Pontaj`.
- Export filtrat bulk: ZIP cu câte un XLSX per magazin, grupat logic după manager
  și firmă, plus manifest cu lună, revision, reguli și checksumuri.
- Export pontaj-only: disponibil pentru filtrul curent.
- Exporturile conțin valori stabile și formule de prezentare compatibile Excel;
  nu au legături externe către Google/Retail.
- Un export închis este legat de exacta revizie de close și este reproductibil.

## 10. Non-goals inițiale

- editor sau cont de agent în aplicație;
- cereri/aprobări de schimb de tură;
- pontaj biometric sau confirmare individuală de prezență;
- ture multiple în aceeași zi;
- mai mulți agenți simultan într-un magazin;
- editare complet bidirecțională Google Sheets;
- calcul din codul POS ca identitate personală;
- integrare directă în Retail înainte ca aplicația standalone să treacă pilotul.

## 11. Decizii confirmate înainte de Stage 3

Formula legacy și Pontajul standard sunt documentate din sursele V1/V2, iar
următoarele autorități au fost confirmate pentru Stage 3:

- salariul fix și tichetele: master HR/payroll effective-dated;
- ajustarea legacy `Flip`: rămâne activă și versionată în calcul;
- sărbătorile: calendar legal România versionat cu override admin, inițial doar
  marker informativ fără efect automat asupra programului, Pontajului, targetului
  sau plății;
- close: numai admin în prima versiune; reopen admin-only și auditat.

Aceste decizii nu deschid integrarea Retail sau accesul la date live; ele doar
permit definirea și testarea contractului S3 standalone.
