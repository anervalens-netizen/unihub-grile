# UniHub Grile

UniHub Grile este modulul de program, pontaj și calcul al grilelor salariale care
va fi integrat ulterior în UniHub Retail. **În etapa curentă se dezvoltă și se
validează complet separat**, astfel încât integrarea finală în Retail să fie în
principal o operație de conectare a unor contracte/adaptoare stabile, nu o a
doua dezvoltare majoră.

## Obiectiv curent

Ținta programului este un **Standalone Plugin Candidate >= 8.5/10, gata pentru
test pe server**, fără nicio modificare în repository-ul sau runtime-ul
`anervalens-netizen/unihub-retail`.

Retail poate fi inspectat read-only pentru compatibilitate: arhitectură, auth,
capabilities, modele de date, convenții UI și contracte. Până când utilizatorul
deschide explicit milestone-ul de integrare, Grile nu trebuie să modifice,
oprească, deployeze, rebase-uiască sau să introducă dependențe runtime în Retail.

## Ce face Grile

- program lunar pe magazin și persoană;
- `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER`, `OFF`, `LEAVE`;
- pontaj derivat din calendar;
- atribuire deterministă a vânzării magazin/zi către agentul planificat;
- calcul salarial versionat prin rule pack Mobiup;
- E-pay validat și auditat;
- excepții și close/reopen de lună;
- Google Sheets ca proiecție controlată + input E-pay limitat;
- import/export XLSX;
- worker durabil pentru operațiile asincrone.

PostgreSQL și motorul Grile sunt autoritatea pentru program, pontaj, atribuire,
calcul și close. Google Sheets nu este baza de date a aplicației și nu conține
autoritatea formulelor financiare.

## Stare

Aplicația are deja un nucleu funcțional important: model de domeniu, calendar cu
revision/CAS, pontaj, atribuire, rule pack, grile, close/reopen, Google/XLSX și un
frontend standalone apropiat vizual de UniHub Retail.

Programul standalone M0-M8 a ajuns la un candidat istoric certificat, dar
un audit independent pre-server a deschis remedierea #69 înainte de instalare.
Scorul/gate-ul istoric din #4 nu este, singur, autorizație de instalare.

Readiness-ul curent se stabilește din cea mai recentă certificare exact-head;
pentru această fază sursa este issue #69. Etichetele vechi `S1…S7` și trackerul
închis #4 rămân numai dovezi istorice. Production și integrarea Retail rămân în
afara acestei autorizări.

## Surse canonice

Ordinea de autoritate este:

1. [Program Plan — issue #3](https://github.com/anervalens-netizen/unihub-grile/issues/3)
2. [Master Tracker — issue #4](https://github.com/anervalens-netizen/unihub-grile/issues/4)
3. [Contract produs](docs/PRODUCT_CONTRACT.md)
4. [Arhitectură](ARCHITECTURE.md)
5. [Mobiup rule pack](docs/MOBIUP_RULE_PACK.md)
6. [UX / Google / XLSX](docs/UX_EXCEL_SPEC.md)
7. [Contract integrare Retail](docs/RETAIL_INTEGRATION_CONTRACT.md)
8. [Quality gates](docs/QUALITY_GATES.md)
9. [Reguli pentru agenți](AGENTS.md)
10. [Comenzi și operare locală](docs/operations/local-commands.md)

Documentele istorice pot demonstra decizii sau teste anterioare, dar nu pot
contrazice planul #3 sau trackerul #4.

## Arhitectura dorită

```text
               dezvoltare curentă

 Fixture/Contract adapters
          |
          v
+-------------------------------+
|       UniHub Grile            |
| API -> services -> domain     |
|          |                    |
|     PostgreSQL                |
|          |                    |
|        worker                 |
|       /       \               |
| Google Sheets  XLSX           |
+-------------------------------+

         integrare ulterioară

 UniHub Retail
  | identity/capabilities
  | catalog/scope
  | sales/targets/incentives
  v
 Retail adapters -> aceleași contracte Grile
```

Domain-ul Grile nu trebuie să știe dacă inputul provine din fixture sau din
Retail. Aceasta este condiția principală pentru un plug-in final cu risc mic.

## Workflow de lucru

Pentru orice batch de dezvoltare:

1. citește issue #3 și #4;
2. selectează taskurile `READY` cu prioritatea cea mai mare;
3. inspectează `main` și codul relevant;
4. lucrează pe branch/PR focalizat;
5. rulează verificările relevante înainte de merge;
6. PR-ul enumeră task IDs, contractele schimbate, testele și limitările;
7. după merge, trackerul #4 se actualizează imediat cu dovezi și următorul pas.

Nu se creează trackere paralele. Nu se marchează un task complet doar pentru că
există cod; este necesară dovada cerută de quality gates.

## Quick start local

```bash
make install
make pg-up
make migrate
make test
make build
```

Pentru API și frontend:

```bash
make api
make web
```

Detalii și limitări: `docs/operations/local-commands.md`.

## Gate final înainte de test pe server

Candidate-ul poate fi declarat gata numai dacă:

- scorul total este `>= 8.5/10`;
- nu există P0/P1 deschis pe correctness, authorization, data loss sau close;
- CI obligatoriu este verde pe exact commitul candidat;
- reconcilierea shadow nu are diferențe salariale neexplicate;
- fluxurile principale sunt validate end-to-end;
- contractul de integrare Retail este complet;
- `unihub-retail` a rămas nemodificat pe durata acestui program.
