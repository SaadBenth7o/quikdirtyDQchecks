# Dremio DQ POC — Data Quality Checks

Framework de **Data Quality** (DQ) pour automatiser les vérifications de qualité des données sur les tables VIRTUALISATION de Dremio.

---

## Vue d'ensemble

Ce projet est un **POC (Proof of Concept)** qui commence par couvrir les vérifications de **complétude**, mais est conçu pour supporter d'autres règles DQ au fur et à mesure :

- ✅ **Complétude** — Vérifier les valeurs non-null et non-vides (actuel)
- ⏳ Unicité, Conformité patterns, Validité métier, etc. (futur)

**Scope actuel** : Vérifications sur **données des tiers** (CIHOne.CLIENTS) mappées en VIRTUALISATION.

### ⚠️ Colonnes KYC et CSP

Les colonnes sensibles au **Know Your Customer (KYC)** et aux **Catégories Socio-Professionnelles (CSP)** nécessitent une attention particulière — impacts réglementaires directs sur la conformité.

---

## Scores & Flags

| Score | Flag | Signification |
|-------|------|---|
| ≥ 90% | **PASS** ✓ | Excellente qualité |
| ≥ 70% | **WARN** ⚠ | À surveiller |
| < 70% | **FAIL** ✗ | Problème sérieux |

---

## Utilisation rapide

### Run simple (config existante)
\\\ash
python run_dq.py --run
\\\

### Régénérer config + run
\\\ash
python run_dq.py --refresh-config --run
\\\

---
## Pipeline d'exécution

Le workflow complet du projet chaîne 4 étapes automatisées :

### Étape 1 : Lecture Excel → Configuration

```
python run_dq.py --refresh-config
```

- **excel_parser.py** lit la feuille "Quality_checks_poc"
- Filtre les lignes contenant "VIRTUALISATION" dans la colonne SQL
- Crée un **query_id** unique (SHA-256 premiers 12 chars) pour chaque SQL
- Déduplique les requêtes identiques (plusieurs checks peuvent partager la même requête)
- **Génère checks_config.yaml** (source de vérité persistante)

**Résultat :** `checks_config.yaml` avec structure :
\\\yaml
tables:
  table_name:
    checks: [{id, dremio_col, sql_id, ...}]
unique_queries:
  sql_id: {sql: "SELECT ...", used_by: [check_ids]}
\\\

### Étape 2 : Exécution des requêtes

```
python run_dq.py --run
```

- **dq_runner.py** charge `checks_config.yaml`
- Pour chaque **unique_query** (optimisation : pas d'exécution redondante) :
  - **dremio_client.py** envoie POST /api/v3/sql
  - Polling GET /api/v3/job/{jobId} (intervalle 2s, timeout 120s)
  - Récupère résultats : GET /api/v3/job/{jobId}/results
  - Retourne `{total_lignes, valides, score_completude_pct}`

### Étape 3 : Calcul des scores & flags

Calculs **bottom-up** :

- **Colonne** : `score = (valides / total_lignes) * 100`
- **Table** : `score = avg(colonnes_scores)` ; `total_lignes = max(colonnes_total_lignes)`
- **Global** : `score = avg(tables_scores)`

**Assignation de flag** :
- ✅ PASS si score ≥ 90%
- ⚠️ WARN si 70% ≤ score < 90%
- ✗ FAIL si score < 70%

### Étape 4 : Génération YAML et CSV

**yaml_writer.py** + **csv_writer.py** créent :

- **Sous-dossier YAML** : `output/{timestamp}/{timestamp}_yaml/`
  - **1 fichier YAML par table** : `{table}.yaml` (format nested)
  - **1 résumé global YAML** : `_all_tables.yaml`
  
- **Sous-dossier CSV** : `output/{timestamp}/{timestamp}_csv/`
  - **1 fichier CSV par table** : `{table}.csv` (importable Excel/Pandas)
  - **1 résumé global CSV** : `_all_tables.csv`

- **Timestamp ISO 8601 avec séparateur ":"** : `2026-06-04_09:50:54`
- **Fichier de log** : `output/{timestamp}/run.log`

### Flux global

\\\
Excel (Quality_checks_poc)
        ↓
   [--refresh-config]
        ↓
  excel_parser.py
        ↓
  checks_config.yaml (configuration persistante)
        ↓
   [--run]
        ↓
  dq_runner.py + dremio_client.py
        ↓
  Exécution requêtes + calcul scores
        ↓
  yaml_writer.py
        ↓
  output/{timestamp}/ (12 tables + résumé global)
\\\

---
## Installation

### 1. Configuration Dremio
Créer \.env\ :
\\\env
DREMIO_HOST=http://dlakegtwprd:9047
DREMIO_AUTH_TYPE=bearer
DREMIO_API_KEY=<votre_clé_API>
SCORE_PASS_THRESHOLD=90
SCORE_WARN_THRESHOLD=70
EXCEL_FILE=Stewardship_Workshop_Template_Tiers_Counterparties_fixed.xlsx
EXCEL_SHEET=Quality_checks_poc
\\\

### 2. Dépendances
\\\ash
pip install -r requirements.txt
\\\

---
## Structure du projet

\\\
QuikDirtyPocDQ/
├── run_dq.py                                          # CLI principal (entrée)
├── lib/
│   ├── __init__.py
│   ├── excel_parser.py                                # Parsing Excel → checks_config.yaml
│   ├── dremio_client.py                               # Wrapper API Dremio REST
│   ├── dq_runner.py                                   # Orchestration + calcul scores
│   ├── yaml_writer.py                                 # Génération outputs YAML
│   └── csv_writer.py                                  # Génération outputs CSV
├── checks_config.yaml                                 # Configuration persistante (généré)
├── Stewardship_Workshop_Template_Tiers_Counterparties_fixed.xlsx
├── .env                                               # Secrets Dremio
├── requirements.txt
├── README.md
└── output/
    └── 2026-06-04_09:50:54/                           # Dernier run (timestamp avec :)
        ├── run.log
        ├── 2026-06-04_09:50:54_yaml/                  # Sous-dossier YAML
        │   ├── _all_tables.yaml
        │   ├── professional_description.yaml
        │   ├── customer_job.yaml
        │   └── ... (10 autres tables)
        └── 2026-06-04_09:50:54_csv/                   # Sous-dossier CSV
            ├── _all_tables.csv
            ├── professional_description.csv
            ├── customer_job.csv
            └── ... (10 autres tables)
\\\

**Structure logique :**
- **run_dq.py** : CLI seul point d'entrée
- **lib/** : Modules internes (imports relatifs `.module`)
  - Facilite réutilisation, organisation claire
  - Chaque module a une responsabilité unique (SoC)

---
## Fichier Excel source

**Sheet : \Quality_checks_poc\**

| Col | Nom | Description |
|-----|-----|---|
| A | domain | Domaine métier |
| B | dataset | Dataset source (CIHOne) |
| C | dremio_col | Nom colonne Dremio |
| D | virt_full_path | Chemin VIRTUALISATION complet |
| E | raw_col | Nom colonne brute VIRTUALISATION |
| F | rule | Type check (ex: "Complétude") |
| G | sql | Requête SQL |

---

## Architecture interne

**Modules (lib/) :**

### 1. **lib/excel_parser.py**
- Parse feuille Excel "Quality_checks_poc"
- Filtre lignes VIRTUALISATION uniquement
- Déduplique SQL par SHA-256 (query_id)
- Génère/charge `checks_config.yaml`
- Fonctions clés : `parse_excel()`, `generate_config()`, `load_config()`

### 2. **lib/dremio_client.py**
- Wrapper classe `DremioClient` pour API REST Dremio
- POST /api/v3/sql → lancement requête
- Polling GET /api/v3/job/{jobId} (2s interval, 120s timeout)
- GET /api/v3/job/{jobId}/results → résultats
- Retourne dict `{total_lignes, valides, score_completude_pct}`
- Gestion erreurs : ArithmeticException, 401 Unauthorized, timeouts

### 3. **lib/dq_runner.py**
- Orchestration principale
- Charge config, boucle sur unique_queries
- Appelle DremioClient pour chaque requête
- Mappe résultats aux checks individuels
- Calcul scores bottom-up : colonne → table → global
- Assigne flags : PASS/WARN/FAIL/_compute_flag()
- Dataclasses : `ColumnResult`, `TableResult`, `RunResult`

### 4. **lib/yaml_writer.py**
- Sérialise `RunResult` en YAML
- Crée **1 fichier par table** : `{table}.yaml` (format nested)
- Crée **1 résumé global** : `_all_tables.yaml`
- Format : ISO 8601 timestamp
- Dossier : `output/{run_timestamp}/`

### 5. **lib/csv_writer.py**
- Exporte `RunResult` en CSV (tabulaire)
- Crée **1 fichier par table** : `{table}.csv` (importable Excel/Pandas)
- Crée **1 résumé global** : `_all_tables.csv`
- Colonnes : dremio_col, virt_full_path, dataset, domain, rule, total_lignes, valides, score_pct, flag

---

## Format des outputs

### YAML par table (`customer_job.yaml`)
\\\yaml
table: customer_job
run_timestamp: '2026-06-04T09:39:12'
total_lignes: 4549263
table_score_pct: 53.1
table_flag: FAIL

columns:
  - dremio_col: code_categorie_socio_professionelle
    score_pct: 45.87
    valides: 2086324
    flag: FAIL
\\\

### CSV par table (`customer_job.csv`)
\\\
dremio_col,virt_full_path,dataset,domain,rule,total_lignes,valides,score_pct,flag,error
code_categorie_socio_professionelle,"VIRTUALISATION.""staging-nova-referentieltiers"".customer_job",CIHOne,Tiers,Complétude,4549263,2086324,45.87,FAIL,
\\\

### Global YAML (`_all_tables.yaml`)
\\\yaml
run_timestamp: '2026-06-04T09:39:12'
global_score_pct: 83.0
global_flag: WARN
total_checks: 51
\\\

### Global CSV (`_all_tables.csv`)
\\\
table,total_lignes,score_pct,flag,nb_checks,nb_pass,nb_warn,nb_fail,nb_error
professional_description,29472,54.30,FAIL,5,2,1,2,0
professional_activity,29463,100.00,PASS,2,2,0,0,0
customer_job,4549263,53.10,FAIL,5,1,0,4,0
\\\

---

## Troubleshooting

| Erreur | Solution |
|--------|----------|
| PermissionError Excel | Fermer le fichier dans Excel |
| ArithmeticException Dremio | Vérifier SQL cible VIRTUALISATION |
| 401 Unauthorized | Vérifier clé API dans .env |
| Timeout 120s | Requête SQL trop lourde |

---

## Workflow : Ajouter un nouveau check

1. Ajouter row dans Excel (colonnes A–G)
2. Lancer \python run_dq.py --refresh-config --run\
3. Consulter \output/{timestamp}/_all_tables.yaml\

---

## Prochaines étapes (roadmap)

- [ ] Support règles DQ supplémentaires (unicité, intégrité ref., conformité patterns)
- [ ] Dashboard web
- [ ] Alertes (email, Slack)
- [ ] Historique des runs et tendances
- [ ] Support multiples sources de données

---

## Notes de conformité

**KYC et CSP** : Ces colonnes ont un impact direct sur la **conformité réglementaire**. Les scores FAIL/WARN nécessitent investigation et documentation des causes racines (données source vs. mapping).

---

License : Internal — Stewardship Workshop POC
Version : 1.0 (POC complétude)
