import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "jobs_database.db")

# On enrichit le dictionnaire pour couvrir tout le spectre Data (ATS-friendly)
ATS_KEYWORDS = {
    "programmation": ["python", "sql", " r ", "vba", "scala", "java", "c++", "bash"],
    "visualisation": ["tableau", "power bi", "looker", "metabase", "plotly", "matplotlib", "seaborn", "qlik"],
    "stack_data": ["spark", "pandas", "numpy", "dbt", "airflow", "dagster", "snowflake", "bigquery", "redshift", "databricks", "hadoop"],
    "machine_learning": ["scikit-learn", "tensorflow", "pytorch", "keras", "mlflow", "nlp", "computer vision", "xgboost", "regression", "clustering"],
    "infrastructure": ["aws", "azure", "gcp", "docker", "kubernetes", "git", "ci/cd", "terraform"],
    "concepts": ["etl", "elt", "data modeling", "data warehousing", "data lake", "data governance", "ab testing", "statistics", "api"],
    "soft_skills": ["agile", "scrum", "communication", "curiosite", "esprit d'equipe", "vulgarisation", "autonomie", "anglais"]
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
    cursor.execute('DROP TABLE IF EXISTS job_offers_silver')
    cursor.execute('''
        CREATE TABLE job_offers_silver (
            id TEXT PRIMARY KEY,
            entreprise TEXT,
            poste TEXT,
            competences_tech TEXT,  -- Liste a plat pour filtres SQL
            competences_json TEXT,  -- Detail structure pour analyse fine
            niveau_etude TEXT,
            top_keyword TEXT        -- Le mot-cle le plus important trouve
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