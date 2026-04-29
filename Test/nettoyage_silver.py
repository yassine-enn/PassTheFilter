import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "jobs_database.db")

# On enrichit le dictionnaire pour couvrir tout le spectre Data (ATS-friendly)
ATS_KEYWORDS = {

    # ── COMMUN DS + DE ─────────────────────────────────────────────
    "programmation": [
        "python", "sql", "r", "scala", "bash", "java", "julia", "matlab"
    ],
    "infrastructure_cloud": [
        "aws", "gcp", "azure", "docker", "kubernetes", "terraform",
        "linux", "git", "github actions", "gitlab ci", "ci/cd"
    ],
    "bases_de_donnees": [
        "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        "cassandra", "neo4j", "sqlite"
    ],
    "soft_skills": [
        "agile", "scrum", "autonomie", "communication", "vulgarisation",
        "anglais", "esprit d'equipe", "gestion de projet",
        "stakeholder management", "curiosite"
    ],

    # ── DATA ENGINEER ───────────────────────────────────────────────
    "de_orchestration": [
        "airflow", "dagster", "prefect", "luigi", "argo workflows"
    ],
    "de_ingestion_streaming": [
        "kafka", "flink", "spark streaming", "kinesis", "pub/sub",
        "debezium", "nifi", "fivetran", "airbyte", "stitch"
    ],
    "de_transformation": [
        "dbt", "spark", "pandas", "polars", "pyspark", "hadoop",
        "hive", "presto", "trino"
    ],
    "de_stockage": [
        "snowflake", "bigquery", "redshift", "databricks", "delta lake",
        "duckdb", "iceberg", "hudi", "data lake", "data lakehouse",
        "data warehouse"
    ],
    "de_concepts": [
        "etl", "elt", "data modeling", "data pipeline", "batch processing",
        "stream processing", "medallion architecture", "data mesh",
        "data fabric", "data catalog", "data lineage", "data quality",
        "reverse etl", "cdc", "idempotence", "partitioning"
    ],

    # ── DATA SCIENTIST ───────────────────────────────────────────────
    "ds_ml_frameworks": [
        "scikit-learn", "tensorflow", "pytorch", "keras", "xgboost",
        "lightgbm", "catboost", "statsmodels", "prophet"
    ],
    "ds_mlops": [
        "mlflow", "bentoml", "seldon", "kubeflow", "sagemaker",
        "vertex ai", "azure ml", "wandb", "dvc", "feature store"
    ],
    "ds_techniques": [
        "regression", "classification", "clustering", "random forest",
        "gradient boosting", "time series", "anomaly detection",
        "recommendation system", "a/b testing", "causal inference",
        "optimisation", "simulation"
    ],
    "ds_nlp_vision": [
        "nlp", "computer vision", "transformers", "bert", "llm",
        "rag", "langchain", "llamaindex", "stable diffusion",
        "opencv", "hugging face", "fine-tuning", "embeddings"
    ],
    "ds_stats": [
        "statistiques bayesiennes", "test hypothese", "p-value",
        "intervalle de confiance", "distribution", "probabilites",
        "econometrie", "series temporelles"
    ],
    "ds_data_analysis": [
        "pandas", "numpy", "scipy", "polars", "jupyter",
        "exploratory data analysis", "feature engineering",
        "data wrangling", "data cleaning"
    ],
    "ds_visualisation": [
        "matplotlib", "seaborn", "plotly", "tableau", "power bi",
        "looker", "streamlit", "dash", "grafana", "superset"
    ],

    # ── GOUVERNANCE & ARCHITECTURE (senior / lead) ──────────────────
    "gouvernance": [
        "data governance", "data stewardship", "rgpd", "data privacy",
        "data security", "master data management", "data contract"
    ],
    "architecture": [
        "data architecture", "lambda architecture", "kappa architecture",
        "microservices", "api rest", "graphql", "event-driven"
    ],
}

def extract_ats_tags(text):
    text = text.lower()
    extracted = {}
    
    for category, keywords in ATS_KEYWORDS.items():
        found_in_cat = []
        for kw in keywords:
            # Recherche stricte du mot (regex \b pour eviter les correspondances partielles)
            if re.search(rf'\b{re.escape(kw.strip())}\b', text):
                found_in_cat.append(kw.strip())
        if found_in_cat:
            extracted[category] = list(set(found_in_cat))
    return extracted

def bronze_to_silver_ats():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Mise a jour de la structure Silver
    # cursor.execute('DROP TABLE IF EXISTS job_offers_silver')
    # cursor.execute('''
    #     CREATE TABLE job_offers_silver (
    #         id TEXT PRIMARY KEY,
    #         entreprise TEXT,
    #         poste TEXT,
    #         competences_tech TEXT,  -- Liste a plat pour filtres SQL
    #         competences_json TEXT,  -- Detail structure pour analyse fine
    #         niveau_etude TEXT,
    #         top_keyword TEXT        -- Le mot-cle le plus important trouve
    #     )
    # ''')
    cursor.execute("DROP TABLE IF EXISTS job_offers_silver")
    cursor.execute('''
           CREATE TABLE job_offers_silver (
               id            TEXT PRIMARY KEY,
               entreprise    TEXT,
               poste         TEXT,
               profil        TEXT,
               competences_tech  TEXT,
               competences_json  TEXT,
               top_category  TEXT,
               top_keyword   TEXT
           )
       ''')



    cursor.execute("SELECT * FROM job_offers")
    rows = cursor.fetchall()

    for row in rows:
        desc = row['description'].lower()
        
        # Extraction structuree
        tags_dict = extract_ats_tags(desc)
        
        # On cree une version texte simple pour les recherches SQL (ex: "python, sql, aws")
        all_tags = []
        for cat_list in tags_dict.values():
            all_tags.extend(cat_list)
        
        flat_tags = ", ".join(all_tags)
        
        # On sauvegarde le dictionnaire complet en JSON dans la base
        import json
        json_tags = json.dumps(tags_dict)

        cursor.execute('''
            INSERT INTO job_offers_silver 
            (id, entreprise, poste, competences_tech, competences_json)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            row['id'],
            row['entreprise'],
            row['poste'],
            flat_tags,
            json_tags
        ))

    conn.commit()
    conn.close()
    print(f"SILVER ATS TERMINE : {len(rows)} offres analysees.")

if __name__ == "__main__":
    bronze_to_silver_ats()