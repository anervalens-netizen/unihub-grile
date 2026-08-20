# Mobiup rule pack — contract grilă și Pontaj

Status: contract de compatibilitate pentru Stage 3

Versiune inițială: `mobiup-v1-compat`

Surse read-only: V1 `grile-salarii@c131c21`, writerul/pilotul Retail V2 și
capturile acceptate de utilizator.

Acest document explică regulile Mobiup. Ele trebuie implementate într-un rule
pack versionat și nu hardcodate în domeniul generic UniHub Grile. În caz de
diferență între acest contract și o formulă legacy, Stage 3 se oprește și cere o
decizie business; nu „repară” rezultatul prin aproximare.

## 1. Autorități și intrări

- calendarul managerului decide persoana, magazinul și clasificarea zilei;
- connectorul furnizează vânzarea fizică magazin/zi, targetul lunar al
  magazinului, cantitatea SIM și incentivele;
- condițiile salariale effective-dated furnizează salariul fix și tichetele de
  masă; managerul de program nu le editează;
- Google Sheet furnizează numai cele două cantități E-pay validate;
- calculele folosesc ID-uri stabile de persoană/magazin, niciodată numele sau
  codul POS/TL ca identitate.

V1 păstra salariul fix ca valoare, fără să îl derive. Valorile legacy observate
sunt `2400` și `2600` RON. Acestea sunt dovezi de compatibilitate, nu o listă
hardcodată pentru produsul nou. Tichetele sunt de asemenea intrare lunară; în
capturile de referință valoarea este `480` RON.

## 2. Target și atribuire

Pentru fiecare magazin:

```text
target_zi_magazin = target_lunar_magazin / zile_vânzare_magazin
```

Targetul principal al persoanei este suma targetelor zilnice pentru zilele în
care lucrează în magazinul de bază, atât `NORMAL`, cât și `EXTRA_HOME`.
`EXTRA_OTHER` folosește targetul zilnic al magazinului-gazdă și rămâne separat
de targetul/realizatul principal de acasă.

Întreaga vânzare fizică a magazinului/zi este creditată persoanei din calendar.
Reatribuirea schimbă numai creditul personal; totalul magazinului și companiei
nu se copiază și nu se modifică.

## 3. Comisionul principal

`progres = realizat_principal / target_principal`. Pentru target zero rezultatul
este zero și se ridică o anomalie de date, nu se produce procent infinit.

| Progres | Comision principal |
|---|---:|
| `< 80%` | `0` |
| `>= 80%` și `< 100%` | `3% × realizat_principal` |
| `>= 100%` și `< 120%` | `3% × realizat_principal + 200 RON` |
| `>= 120%` | `3% × realizat_principal + 400 RON` |

Comisionul principal se rotunjește la RON întreg.

## 4. Zile suplimentare

Orice zi clasificată `EXTRA_HOME` sau `EXTRA_OTHER` adaugă plata fixă de
`150 RON/zi`.

- `EXTRA_HOME`: vânzarea și targetul zilei intră în realizatul/targetul principal
  de acasă; nu există încă un al doilea comision procentual pentru aceeași
  vânzare;
- `EXTRA_OTHER`: vânzarea și targetul magazinului-gazdă rămân într-o linie
  separată. Dacă `realizat_zi / target_zi >= 0,79`, se acordă `3% × realizat_zi`;
  altfel comisionul acelei zile este zero.

Pragul `0,79` este pragul tehnic exact din V1 și trebuie păstrat în primul
golden master. Fiecare comision `EXTRA_OTHER` se rotunjește individual la RON
întreg, apoi se însumează. Plata fixă de `150 RON` și comisionul procentual sunt
două componente diferite și trebuie afișate separat în audit.

## 5. SIM, E-pay și incentive

| Componentă | Regulă |
|---|---:|
| SIM eligibil | `3 RON × cantitate` |
| E-pay `<50 lei` | `5 RON × cantitate` |
| E-pay `>=50 lei` | `12 RON × cantitate` |
| Incentive lunar | valoarea autoritativă primită din Campaigns/connector |

Cantitățile E-pay sunt numere întregi `0..10`. SIM nu este introdus manual în
Sheet; vine din sursa de vânzări. Incentivul nu se recalculează din grilă.

## 6. Total salarial

Motorul păstrează componentele separat și calculează:

```text
total_salariu = salariu_fix
              + tichete_masă
              + comision_principal
              + plată_fixă_zile_suplimentare
              + comision_EXTRA_OTHER
              + comision_SIM
              + comision_Epay
              + incentive_lunar
              + ajustare_Flip_dacă_este_activă

salariu_cash = total_salariu - tichete_masă
```

`total_salariu` include tichetele. Acest lucru explică exemplul V2 vizibil:
`2600 + 480 + 27 SIM + 350 incentive = 3457 RON`, când celelalte componente
sunt zero.

Toate calculele monetare folosesc `Decimal`. Compatibilitatea cu Google
`ROUND(...,0)` cere rotunjire `ROUND_HALF_UP` pentru valorile pozitive. Payloadul
de calcul păstrează intrările, componentele nerotunjite/rotunjite, versiunea
rule pack și hashurile deterministe.

## 7. Pontaj standard Mobiup

Pontajul este o proiecție read-only a calendarului. Nu este formular și nu are
o a doua autoritate.

### Structura fixă a tabului `Pontaj`

- zilele `1..31` sunt coloanele `C:AG`;
- coloana `AH` este totalul lunar de ore nete;
- zona compatibilă este `C8:AG31`, adică opt blocuri consecutive de câte trei
  rânduri, cu rândurile de început `8, 11, 14, 17, 20, 23, 26, 29`;
- magazinul standard cu doi agenți folosește blocurile care încep la rândurile
  `8` și `11`; restul rămân goale, dar structura se păstrează pentru export;
- pentru un bloc care începe la rândul `r`: `r` conține orele nete zilnice,
  `r+1` intervalul, iar `r+2` pauza;
- totalul persoanei este `AHr = SUM(Cr:AGr)`. Pentru cei doi agenți standard,
  totalurile sunt `AH8` și `AH11`.

### Proiecția unei zile

| Calendar | rând ore nete | rând interval | rând pauză |
|---|---:|---|---:|
| `NORMAL`, `EXTRA_HOME`, `EXTRA_OTHER` | `11` | `10:00-22:00` | `1` |
| `OFF`, `LEAVE`, zi inexistentă în lună | gol | gol | gol |

Weekendurile se evidențiază galben după luna selectată. Evidențierea nu schimbă
orele. La orice modificare mid-month, Pontajul se reconstruiește din întreaga
revizie a calendarului și înlocuiește proiecția anterioară; nu se face patch pe
celule individuale și nu se păstrează editări manuale din Sheet.

Intervalul `10:00-22:00`, pauza de `60` minute și rezultatul de `11` ore nete
sunt politica standard Mobiup. Motorul generic poate primi alte politici prin
alt rule pack, dar aplicația Mobiup nu prezintă managerului o configurare per
magazin în prima versiune.

## 8. Golden fixtures obligatorii pentru Stage 3

1. progres principal `79,99%`, `80%`, `99,99%`, `100%`, `119,99%`, `120%`;
2. `EXTRA_HOME` cu plata fixă și fără dublarea comisionului;
3. `EXTRA_OTHER` sub `0,79`, exact `0,79` și peste prag;
4. reatribuire între persoane cu total fizic neschimbat;
5. SIM și ambele categorii E-pay la `0`, `1`, `10`;
6. exemplul V2 `2600 + 480 + 27 + 350 = 3457`;
7. schimbare mid-month care mută o zi și reconstruiește Pontajul `11/interval/1`
   și totalurile `AH`;
8. target zero, vânzare lipsă și zi neacoperită ca anomalii explicite;
9. reproducere din același payload/hash cu rezultat identic.

## 9. Decizii încă necesare înainte de S3

- sursa exactă pentru salariul fix și tichete în connectorul standalone
  (recomandat: condiții effective-dated din Retail/HR, nu editare TL);
- dacă ajustarea legacy `Flip` rămâne în produsul nou;
- lista sărbătorilor și dacă influențează targetul, orele sau plata;
- cine execută `close`: recomandat TL pregătește/validează, admin închide și
  redeschide în prima versiune.
