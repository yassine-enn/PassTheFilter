"""
=============================================================
  POC — Scoring ATS de CV pour offres Data Analyst (France)
=============================================================

Architecture :
  1. Chargement de toutes les offres (DB + JSON)
  2. Construction du profil marché (fréquence des keywords)
  3. Extraction des compétences du CV candidat
  4. Scoring multi-dimensionnel
  5. Rapport détaillé avec recommandations

Usage :
    python cv_scorer_poc.py
    (modifier le CV_SAMPLE en bas pour tester avec votre CV)
"""

import json
import re
import sqlite3
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# ─────────────────────────────────────────────
# 1. Configuration ATS (enrichie + corrigée)
# ─────────────────────────────────────────────

ATS_KEYWORDS: Dict[str, List[str]] = {
    "programmation": [
        "python", "sql", "r", "vba", "scala", "java", "c++", "bash",
        "javascript", "typescript", "dax", "m language",
    ],
    "visualisation": [
        "tableau", "power bi", "looker", "metabase", "plotly",
        "matplotlib", "seaborn", "qlik", "d3.js", "grafana", "superset",
    ],
    "stack_data": [
        "spark", "pandas", "numpy", "dbt", "airflow", "dagster",
        "snowflake", "bigquery", "redshift", "databricks", "hadoop",
        "kafka", "flink", "duckdb", "polars",
    ],
    "machine_learning": [
        "scikit-learn", "tensorflow", "pytorch", "keras", "mlflow",
        "nlp", "computer vision", "xgboost", "lightgbm", "regression",
        "clustering", "random forest", "llm", "rag",
    ],
    "infrastructure": [
        "aws", "azure", "gcp", "docker", "kubernetes", "git",
        "ci/cd", "terraform", "gitlab", "github", "linux",
    ],
    "concepts": [
        "etl", "elt", "data modeling", "data warehousing", "data lake",
        "data governance", "a/b testing", "statistiques", "statistics",
        "api", "data mesh", "medallion",
    ],
    "soft_skills": [
        "agile", "scrum", "communication", "curiosité", "esprit d'équipe",
        "vulgarisation", "autonomie", "anglais", "leadership", "rigueur",
    ],
}

# Poids par catégorie pour le score global
CATEGORY_WEIGHTS: Dict[str, float] = {
    "programmation":    0.25,
    "stack_data":       0.20,
    "visualisation":    0.15,
    "machine_learning": 0.15,
    "infrastructure":   0.10,
    "concepts":         0.10,
    "soft_skills":      0.05,
}

# ─────────────────────────────────────────────
# 2. Correctif : extraction du type de contrat
# ─────────────────────────────────────────────

def extract_contract_type(text: str) -> str:
    """
    CORRECTIF du bug original : 'stage' était détecté dans
    'prestage', 'upstage', etc. On utilise des word boundaries.
    """
    text_lower = text.lower()
    if re.search(r'\bstage\b', text_lower):
        return "Stage"
    if re.search(r'\bcdi\b', text_lower):
        return "CDI"
    if re.search(r'\bcdd\b', text_lower):
        return "CDD"
    if re.search(r'\balternance\b', text_lower):
        return "Alternance"
    if re.search(r'\bfreelance\b', text_lower):
        return "Freelance"
    return "N/A"


def extract_education_level(text: str) -> str:
    """Extraction du niveau d'études (manquait dans le pipeline original)."""
    text_lower = text.lower()
    if re.search(r'bac\s*\+?\s*5|master|m2|ingénieur|msc', text_lower):
        return "Bac+5"
    if re.search(r'bac\s*\+?\s*4|m1', text_lower):
        return "Bac+4"
    if re.search(r'bac\s*\+?\s*3|licence|bachelor', text_lower):
        return "Bac+3"
    if re.search(r'bac\s*\+?\s*2|bts|iut|dut', text_lower):
        return "Bac+2"
    return "Non précisé"


# ─────────────────────────────────────────────
# 3. Extraction de keywords (robuste)
# ─────────────────────────────────────────────

def extract_keywords(text: str) -> Dict[str, List[str]]:
    """
    Extrait les keywords ATS d'un texte (offre ou CV).
    CORRECTIF : le langage R est cherché avec un boundary strict.
    """
    text_lower = text.lower()
    extracted: Dict[str, List[str]] = {}

    for category, keywords in ATS_KEYWORDS.items():
        found = []
        for kw in keywords:
            # Cas spécial : "r" seul → très court, risque FP
            if kw == "r":
                pattern = r'\br\b(?!\s*\+\+|\s*#)'  # 'r' mais pas 'r++' ou 'r#'
            else:
                pattern = rf'\b{re.escape(kw.strip())}\b'
            if re.search(pattern, text_lower):
                found.append(kw)
        if found:
            extracted[category] = list(set(found))

    return extracted


# ─────────────────────────────────────────────
# 4. Chargement de toutes les données
# ─────────────────────────────────────────────

@dataclass
class JobOffer:
    id: str
    titre: str
    entreprise: str
    contrat: str
    niveau_etude: str
    keywords: Dict[str, List[str]]
    source: str


def load_all_offers(base_dir: str) -> List[JobOffer]:
    """
    Charge toutes les offres depuis :
    - La DB SQLite (table job_offers)
    - all_data_offers.json (380 offres)
    - hellowork_data_analyst_france.json (100 offres)
    """
    offers: List[JobOffer] = []
    seen_ids: set = set()

    # ── A. Depuis la DB ──
    db_path = os.path.join(base_dir, "jobs_database.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM job_offers")
        for row in cursor.fetchall():
            oid = str(row["id"])
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            desc = row["description"] or ""
            offers.append(JobOffer(
                id=oid,
                titre=row["poste"] or "",
                entreprise=row["entreprise"] or "",
                contrat=extract_contract_type(desc),     # correctif
                niveau_etude=extract_education_level(desc),  # nouveau
                keywords=extract_keywords(desc),
                source="db_wttj",
            ))
        conn.close()

    # ── B. Depuis all_data_offers.json ──
    for json_file, source_label in [
        ("all_data_offers.json", "meteojob"),
        ("hellowork_data_analyst_france.json", "hellowork"),
    ]:
        json_path = os.path.join(base_dir, json_file)
        if not os.path.exists(json_path):
            continue
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        raw_offers = data.get("offers", [])
        for o in raw_offers:
            oid = str(o.get("id", ""))
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            title = o.get("title", "")
            company = o.get("company", "")
            # hellowork n'a pas de champ company → on parse le titre
            if not company and " - " in title:
                parts = title.split(" - ")
                title = parts[0].strip()
                company = parts[1].strip()
            desc_raw = o.get("description_raw", "")
            desc = (o.get("description_poste") or "") + " " + (o.get("description_profil") or "")
            full_desc = desc_raw if len(desc_raw) > len(desc) else desc
            offers.append(JobOffer(
                id=oid,
                titre=title,
                entreprise=company,
                contrat=extract_contract_type(full_desc),
                niveau_etude=extract_education_level(full_desc),
                keywords=extract_keywords(full_desc),
                source=source_label,
            ))

    return offers


# ─────────────────────────────────────────────
# 5. Construction du profil marché
# ─────────────────────────────────────────────

@dataclass
class MarketProfile:
    total_offers: int
    keyword_frequency: Dict[str, Dict[str, float]]   # cat → kw → %
    category_coverage: Dict[str, float]               # cat → % offres qui en parlent
    top_keywords: List[Tuple[str, float]]             # global top 20
    education_dist: Dict[str, float]
    contract_dist: Dict[str, float]


def build_market_profile(offers: List[JobOffer]) -> MarketProfile:
    """Calcule la fréquence de chaque keyword dans l'ensemble des offres."""
    n = len(offers)
    if n == 0:
        raise ValueError("Aucune offre chargée.")

    kw_counts: Dict[str, Dict[str, int]] = defaultdict(Counter)
    cat_counts: Dict[str, int] = Counter()
    all_kw_counts: Counter = Counter()
    edu_counts: Counter = Counter()
    contract_counts: Counter = Counter()

    for offer in offers:
        edu_counts[offer.niveau_etude] += 1
        contract_counts[offer.contrat] += 1
        for cat, kws in offer.keywords.items():
            cat_counts[cat] += 1
            for kw in kws:
                kw_counts[cat][kw] += 1
                all_kw_counts[kw] += 1

    # Normalisation en fréquences relatives
    kw_frequency = {
        cat: {kw: count / n for kw, count in cat_kws.items()}
        for cat, cat_kws in kw_counts.items()
    }
    category_coverage = {cat: count / n for cat, count in cat_counts.items()}
    top_keywords = [(kw, count / n) for kw, count in all_kw_counts.most_common(20)]

    return MarketProfile(
        total_offers=n,
        keyword_frequency=kw_frequency,
        category_coverage=category_coverage,
        top_keywords=top_keywords,
        education_dist={k: v / n for k, v in edu_counts.items()},
        contract_dist={k: v / n for k, v in contract_counts.items()},
    )


# ─────────────────────────────────────────────
# 6. Scoring du CV
# ─────────────────────────────────────────────

@dataclass
class ScoreResult:
    overall: float                             # 0–100
    category_scores: Dict[str, float]          # cat → 0–100
    matched_keywords: Dict[str, List[str]]     # cat → liste
    missing_keywords: Dict[str, List[str]]     # cat → top manquants
    market_bonus: Dict[str, float]             # kw → fréquence dans offres
    education_match: str
    contract_targets: List[str]
    recommendations: List[str]


def score_cv(
    cv_text: str,
    market: MarketProfile,
    target_contract: str = None,
) -> ScoreResult:
    """
    Score le CV sur 0–100 en fonction du profil marché.

    Logique :
    - Pour chaque catégorie : score = Σ(fréquence_marché des kw trouvés)
      normalisé par Σ(fréquence_marché de TOUS les kw de la catégorie)
    - Score global = moyenne pondérée des scores catégorie
    - Bonus de +5 si le niveau d'études correspond au marché dominant
    """
    cv_keywords = extract_keywords(cv_text)
    cv_education = extract_education_level(cv_text)

    category_scores: Dict[str, float] = {}
    matched: Dict[str, List[str]] = {}
    missing: Dict[str, List[str]] = {}
    market_bonus: Dict[str, float] = {}

    for cat, all_kws in ATS_KEYWORDS.items():
        freq_map = market.keyword_frequency.get(cat, {})
        # Score numérateur = somme des fréquences marché des kw du CV dans cette cat
        cv_cat_kws = set(cv_keywords.get(cat, []))
        matched[cat] = list(cv_cat_kws)
        numerator = sum(freq_map.get(kw, 0) for kw in cv_cat_kws)
        # Dénominateur = somme de toutes les fréquences de cette catégorie
        denominator = sum(freq_map.values()) if freq_map else 1
        cat_score = min(numerator / denominator, 1.0) * 100 if denominator > 0 else 0

        # Si aucune offre ne mentionne cette catégorie → score parfait (non pénalisé)
        if cat not in market.keyword_frequency:
            cat_score = 100.0

        category_scores[cat] = round(cat_score, 1)

        # Top keywords manquants (triés par fréquence marché)
        all_missing = [kw for kw in ATS_KEYWORDS[cat] if kw not in cv_cat_kws]
        missing[cat] = sorted(
            all_missing,
            key=lambda k: freq_map.get(k, 0),
            reverse=True
        )[:5]  # top 5 manquants

        # Market bonus : fréquence des kw trouvés dans le marché
        for kw in cv_cat_kws:
            if kw in freq_map:
                market_bonus[kw] = round(freq_map[kw] * 100, 1)

    # Score global pondéré
    overall = sum(
        category_scores.get(cat, 0) * weight
        for cat, weight in CATEGORY_WEIGHTS.items()
    )

    # Bonus éducation (+5 si Bac+5 et le marché en demande majoritairement)
    dominant_edu = max(market.education_dist, key=market.education_dist.get)
    if cv_education == dominant_edu:
        overall = min(overall + 5, 100)

    # Recommandations
    recommendations = _generate_recommendations(
        category_scores, missing, market, cv_education, dominant_edu, overall
    )

    # Contrats cibles (top 2 du marché)
    top_contracts = sorted(
        market.contract_dist.items(), key=lambda x: x[1], reverse=True
    )
    contract_targets = [c for c, _ in top_contracts[:2] if c != "N/A"]

    return ScoreResult(
        overall=round(overall, 1),
        category_scores=category_scores,
        matched_keywords=matched,
        missing_keywords=missing,
        market_bonus=market_bonus,
        education_match=f"{cv_education} (marché dominant : {dominant_edu})",
        contract_targets=contract_targets,
        recommendations=recommendations,
    )


def _generate_recommendations(
    cat_scores, missing, market, cv_edu, dominant_edu, overall
) -> List[str]:
    recs = []

    # Recommandations par catégorie faible
    weak_cats = sorted(
        [(c, s) for c, s in cat_scores.items() if s < 40],
        key=lambda x: x[1]
    )
    for cat, score in weak_cats[:3]:
        top_missing = missing.get(cat, [])[:3]
        if top_missing:
            pct = market.category_coverage.get(cat, 0) * 100
            recs.append(
                f"⚠️  Catégorie '{cat}' faible ({score:.0f}/100, "
                f"présente dans {pct:.0f}% des offres). "
                f"Ajoutez : {', '.join(top_missing)}"
            )

    # Éducation
    if cv_edu != dominant_edu:
        recs.append(
            f"📚 Le marché cible principalement '{dominant_edu}' "
            f"({market.education_dist.get(dominant_edu, 0)*100:.0f}% des offres). "
            f"Votre profil : '{cv_edu}'."
        )

    # Score global
    if overall >= 75:
        recs.append("✅ Score excellent — votre profil est très aligné avec le marché.")
    elif overall >= 55:
        recs.append("🟡 Score correct — quelques ajouts ciblés peuvent booster votre profil.")
    else:
        recs.append("🔴 Score faible — priorisez les compétences manquantes à fort impact marché.")

    return recs


# ─────────────────────────────────────────────
# 7. Affichage du rapport
# ─────────────────────────────────────────────

def print_report(result: ScoreResult, market: MarketProfile) -> None:
    bar_len = 30

    def bar(score: float) -> str:
        filled = int(score / 100 * bar_len)
        return "█" * filled + "░" * (bar_len - filled)

    print("\n" + "=" * 65)
    print("  RAPPORT ATS — SCORING CV")
    print("=" * 65)
    print(f"\n  📊 Score global : {result.overall:.1f} / 100")
    print(f"  {bar(result.overall)}")
    print(f"\n  📁 Base de référence : {market.total_offers} offres d'emploi")
    print(f"  🎓 Formation : {result.education_match}")
    print(f"  🎯 Types de contrat cibles : {', '.join(result.contract_targets)}")

    print("\n" + "-" * 65)
    print("  SCORES PAR CATÉGORIE")
    print("-" * 65)
    for cat in ATS_KEYWORDS:
        score = result.category_scores.get(cat, 0)
        found = result.matched_keywords.get(cat, [])
        print(f"\n  {cat.upper():<20} {score:>5.1f}/100  {bar(score)}")
        if found:
            bonus_info = [
                f"{kw} ({result.market_bonus.get(kw, 0):.0f}%)"
                for kw in found
            ]
            print(f"    ✓ Trouvé  : {', '.join(bonus_info)}")
        top_miss = result.missing_keywords.get(cat, [])[:3]
        if top_miss:
            print(f"    ✗ Manque  : {', '.join(top_miss)}")

    print("\n" + "-" * 65)
    print("  TOP 15 KEYWORDS DU MARCHÉ")
    print("-" * 65)
    for kw, freq in market.top_keywords[:15]:
        marker = "✓" if kw in result.market_bonus else " "
        print(f"  {marker} {kw:<25} {freq*100:>5.1f}% des offres")

    print("\n" + "-" * 65)
    print("  RECOMMANDATIONS")
    print("-" * 65)
    for rec in result.recommendations:
        print(f"\n  {rec}")

    print("\n" + "=" * 65)


# ─────────────────────────────────────────────
# 8. Point d'entrée
# ─────────────────────────────────────────────

# ── CV de test : remplacez par le vrai contenu ──
CV_SAMPLE = """
Jean Dupont — Data Analyst
Paris, France | jean@email.com

FORMATION
Master 2 Data Science — Université Paris-Saclay (2023)

COMPÉTENCES TECHNIQUES
- Langages : Python, SQL, R
- Visualisation : Power BI, Matplotlib, Seaborn
- Stack : Pandas, Numpy, Scikit-learn, DBT
- Cloud : AWS (S3, Glue), Git, Docker
- Concepts : ETL, A/B testing, statistiques

EXPÉRIENCES
Data Analyst — Startup Fintech (2023–2025)
  - Développement de dashboards Power BI pour le suivi des KPIs
  - Modèles de régression et clustering pour la segmentation clients
  - Mise en place d'un pipeline ETL avec Airflow et BigQuery

SOFT SKILLS
Communication, autonomie, curiosité, anglais (C1)
"""

if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    print("⏳ Chargement des offres...")
    offers = load_all_offers(BASE_DIR)
    print(f"✅ {len(offers)} offres chargées")

    print("⏳ Construction du profil marché...")
    market = build_market_profile(offers)

    print("⏳ Scoring du CV...")
    result = score_cv(CV_SAMPLE, market)

    print_report(result, market)