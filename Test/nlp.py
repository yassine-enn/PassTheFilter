import json
import spacy
from collections import Counter

with open("job_offers_wttj.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Exemple : prendre la première offre
job = data["offers"][0]
text = job["description_raw"]

def extract_relevant_section(text):
    # Normaliser le texte en minuscules pour la recherche
    text_lower = text.lower()
    
    start_marker = "le poste"
    end_marker = "profil recherché"

    start_idx = text_lower.find(start_marker)
    end_idx = text_lower.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        # Si on ne trouve pas les marqueurs, on renvoie tout
        return text

    # Extraire le texte entre les deux
    relevant_text = text[start_idx + len(start_marker):end_idx]
    return relevant_text.strip()

# Exemple sur une offre
job_text = job["description_raw"]
job_text_clean = extract_relevant_section(job_text)

print(job_text_clean[:1000])  # Afficher un extrait
