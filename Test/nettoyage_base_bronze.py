import json
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_JSON = os.path.join(BASE_DIR, "job_offers_wttj_clean_v4.json")
DEST_DB = os.path.join(BASE_DIR, "jobs_database.db")

def clean_raw_text(text):
    """Supprime le bruit inutile des cookies au debut du texte raw"""
    if "Axeptio consent" in text:
        # On coupe tout ce qui est avant la plateforme de gestion de consentement
        parts = text.split("Axeptio consent")
        return parts[-1].strip()
    return text

def run_pipeline():
    if not os.path.exists(SOURCE_JSON):
        print(f"ERREUR : Fichier introuvable : {SOURCE_JSON}")
        return

    with open(SOURCE_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(DEST_DB)
    cursor = conn.cursor()

    # On recrée la table proprement
    cursor.execute('DROP TABLE IF EXISTS job_offers')
    cursor.execute('''
        CREATE TABLE job_offers (
            id TEXT PRIMARY KEY,
            entreprise TEXT,
            poste TEXT,
            description TEXT,
            contrat TEXT,
            url TEXT,
            date_scrap TEXT
        )
    ''')

    new_entries = 0
    for offer in data.get("offers", []):
        parts = offer.get("title", "").split(" - ")
        poste = parts[0].strip() if len(parts) > 0 else "N/A"
        entreprise = parts[1].strip() if len(parts) > 1 else "Inconnue"
        
        # ON PREND TOUT : description_raw est la plus complete
        # Mais on la nettoie un peu pour virer les mentions de cookies
        full_text = clean_raw_text(offer.get("description_raw", ""))
        
        # Si description_raw est vide, on se rabat sur le combiné
        if len(full_text) < 100:
            full_text = f"{offer.get('description_poste', '')}\n{offer.get('description_profil', '')}"

        try:
            cursor.execute('''
                INSERT INTO job_offers (id, entreprise, poste, description, contrat, url, date_scrap)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                offer.get("id"),
                entreprise,
                poste,
                full_text,
                offer.get("metadata", {}).get("contract_type", "N/A"),
                offer.get("url"),
                data.get("scraped_at", "N/A")
            ))
            new_entries += 1
        except sqlite3.IntegrityError:
            continue

    conn.commit()
    conn.close()
    print(f"TERMINÉ : {new_entries} offres traitees.")

if __name__ == "__main__":
    run_pipeline()