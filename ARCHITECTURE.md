# Arhitectură UniHub Grile

## 1. Poziționare

UniHub Grile este un produs opțional și separat de UniHub Retail. Prima
configurație este Mobiup/Mobicell, dar regulile clientului nu trebuie hardcodate
în transport, persistență sau interfața de integrare.

Nu este un plugin Python/JavaScript încărcat în procesul Retail. Integrarea
ulterioară în Retail va fi o capabilitate activabilă care deschide aplicația
Grile și schimbă date printr-un contract versionat.

## 2. Componente

```text
┌──────────────────────── Client source ────────────────────────┐
│ Retail connector v1 / fixture connector / viitor connector    │
└──────────────────────────────┬─────────────────────────────────┘
                               v
┌──────────────────────── UniHub Grile ─────────────────────────┐
│ API web                                                       │
│  ├─ calendar, filtre, detalii magazin/agent, close            │
│  ├─ import preview/apply                                      │
│  └─ export și stare joburi                                    │
│                                                               │
│ Domain engine                                                 │
│  ├─ acoperire magazin/zi și calendar persoană                 │
│  ├─ pontaj derivat                                            │
│  ├─ atribuire vânzare magazin/zi -> persoană                  │
│  ├─ suplimentar acasă / în alt magazin                        │
│  ├─ E-pay și calcul grilă                                     │
│  └─ validare, close, reopen, audit                            │
│                                                               │
│ PostgreSQL                                                    │
│  ├─ adevăr business                                           │
│  ├─ generații și read models                                  │
│  ├─ outbox/job state                                          │
│  └─ audit append-only                                         │
│                                                               │
│ Un singur worker durabil                                      │
│  ├─ ingest snapshot sursă                                     │
│  ├─ Google projection/read E-pay                              │
│  └─ XLSX import/export                                        │
└──────────────────────────────┬─────────────────────────────────┘
                               v
┌──────────────────────── Google Sheets ────────────────────────┐
│ Sheet 1 Grila: rezultate + calendar + 4 celule E-pay editabile│
│ Sheet 2 Pontaj: proiecție derivată, fără editare               │
└────────────────────────────────────────────────────────────────┘
```

## 3. Autorități și direcții de date

| Date | Autoritate | Direcție |
|---|---|---|
| Magazine, ierarhie, persoane, coduri externe | connector client | client -> Grile |
| Vânzări fizice magazin/zi | connector client | client -> Grile |
| Program și clasificare suplimentară | Grile | Manager UI/Excel -> Grile |
| Pontaj | Grile, derivat din calendar | Grile -> UI/Sheet/XLSX |
| Credit comercial personal | Grile, derivat | Grile -> UI/Sheet/XLSX |
| Cantități E-pay | Grile după ingest validat | Sheet -> Grile |
| Calcul salarial și stare lună | Grile | Grile -> UI/Sheet/XLSX |

Google Sheets nu este citit în requesturile normale ale interfeței. UI citește
doar PostgreSQL/read models. Google I/O rulează în worker și publică o proiecție
nouă numai după succes complet; o eroare păstrează ultima versiune bună.

## 4. Model minim de domeniu

- `tenants`: configurație client și timezone.
- `users`, `roles`, `manager_scopes`: admin și TL cu magazine permise.
- `stores`: cod intern, cod extern, firmă, nume, manager, activitate.
- `people`: identitate internă stabilă, cod extern, magazin de bază, activitate.
- `store_assignments`: apartenența normală persoană–magazin, effective-dated.
- `months`: `DRAFT`, `OPEN`, `CLOSED`, `REOPENED`, revision.
- `site_day_assignments`: o persoană activă per magazin/zi, clasificare
  `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER`.
- `person_day_absences`: `OFF`, `LEAVE`; nu dublează o zi lucrată.
- `sales_store_day`: vânzarea fizică imuabilă per magazin/zi/generație sursă.
- `sales_person_day`: proiecția creditului către persoana din calendar.
- `epay_observations`: cele două cantități, sursă, valoare și moment.
- `grid_calculations`: intrări, versiune reguli și rezultate deterministe.
- `sheet_bindings`, `sheet_projection_runs`: legături permanente și stare sync.
- `import_runs`, `export_runs`: manifest, validare, rezultat și fișier.
- `audit_events`: append-only pentru schimbări business și administrative.

Toate tabelele business includ `tenant_id`. Regulile Mobiup sunt un rule pack
versionat, nu condiții dispersate în cod.

## 5. Invariante tranzacționale

1. Un magazin are exact un agent lucrător pe fiecare zi în care magazinul este
   deschis; zero este permis numai ca excepție vizibilă în draft.
2. O persoană nu poate lucra în două magazine în aceeași zi.
3. `EXTRA_OTHER` presupune `person.home_store_id != site_id`.
4. `EXTRA_HOME` presupune `person.home_store_id == site_id`.
5. Întreaga vânzare `sales_store_day` este creditată agentului planificat.
6. Reatribuirea modifică doar proiecția personală; nu copiază și nu schimbă
   totalul fizic magazin/companie.
7. Pontajul este reconstruit în aceeași tranzacție/revizie cu calendarul.
8. O lună `CLOSED` respinge toate schimbările business și ingestul E-pay.
9. Reopen este admin-only, cere motiv și creează audit; nu șterge close-ul vechi.
10. Importul Excel este preview + apply atomic cu revision/CAS; fișierul invalid
    nu produce scrieri parțiale.

## 6. Pontaj derivat

Pontajul nu are editor separat. Pentru fiecare persoană și zi, motorul proiectează
valoarea vizibilă pe baza calendarului și a rule pack-ului clientului:

- zi lucrată Mobiup: `11` ore nete, interval `10:00–22:00`, pauză `1` oră;
- liber/concediu: celulele celor trei rânduri sunt goale;
- weekend: aceeași evidențiere galbenă ca modelul furnizat;
- total: `AH` însumează rândul de ore nete.

Dacă managerul modifică programul la mijlocul lunii, aceeași revizie actualizează
calendarul, pontajul, creditul de vânzări și grila. Proiecția Google este apoi
regenerată asincron.

Schema exactă `C8:AG31`, blocurile de câte trei rânduri și golden fixtures sunt
definite în `docs/MOBIUP_RULE_PACK.md`. Politica nu este configurabilă de TL per
magazin; un viitor client primește alt rule pack versionat.

## 7. Google Sheets

- Legăturile permanente se păstrează în `sheet_bindings`.
- Sheet 1 păstrează identitatea vizuală V2: rezumat magazin, două carduri de
  agent, salariu/proiecție, calendar, concedii și suplimentare.
- Sheet 2 păstrează forma pontajului din captura furnizată: rânduri persoană,
  rând pauză, zile pe coloane, weekend evidențiat și total final.
- Formula business complexă este eliminată treptat; aplicația scrie valori și
  formule de prezentare simple.
- Întregul fișier este protejat, cu excepția a patru dropdown-uri de cantitate:
  două categorii E-pay pentru fiecare dintre cei doi agenți.
- Categorii inițiale: `<50 lei` și `>=50 lei`; valori întregi `0..10`.
- Editarea anonimă nu oferă identitate. Observația este atribuită foii/magazinului
  și devine adevăr numai după validare și ingest.
- La fiecare sync normal se poate citi E-pay. Readback-ul este obligatoriu la
  refresh explicit și imediat înainte de close.

## 8. Performanță și reziliență

- Overview-ul lunar este un read model precomputat; nu face query per magazin.
- Filtrarea după manager/firmă/magazin este server-side și indexată.
- Calendarul mare folosește virtualizare și răspuns compact pe lună.
- Joburile Google/XLSX sunt idempotente, cu revision, retry limitat și stare
  vizibilă; nu există polling Google frecvent sau worker separat per funcție.
- O generație sursă incompletă nu devine zero și nu înlocuiește ultima generație
  bună.
- Exporturile mari sunt create în worker și livrate ca fișiere bounded/streamed.

## 9. Integrarea viitoare cu Retail

Stage 1–6 folosesc fixture connector și nu modifică Retail. Contractul final va
fi versionat și va transporta cel puțin:

- tenant, lună, timezone și generație;
- magazine și ierarhie;
- persoane, coduri și apartenență normală;
- vânzări magazin/zi;
- targete și rezultate de campanie necesare grilei.

Retail nu importă tabelele sau regulile Grile. La Stage 7 poate expune un link de
capabilitate/autentificare și endpointul/outbox-ul contractului. Accesul direct la
schema Retail este permis numai unui adaptor tranzitoriu read-only și nu poate fi
arhitectura finală.
