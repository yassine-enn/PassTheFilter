import json

# Charger le JSON
with open("job_offers_wttj.json", "r", encoding="utf-8") as f:
    data = json.load(f)

def extract_sections(text):
    """
    Retourne deux sections : poste et profil recherché
    - Commence à "Le poste"
    - Profil recherché commence à "Profil recherché"
    - Coupe tout après "en savoir plus"
    """
    text_lower = text.lower()
    cutoff_marker = "en savoir plus"
    
    # Trouver la position du cutoff
    cutoff_idx = text_lower.find(cutoff_marker)
    final_text = text if cutoff_idx == -1 else text[:cutoff_idx]

    # Trouver début des sections
    poste_marker = "le poste"
    profil_marker = "profil recherché"

    start_poste = final_text.lower().find(poste_marker)
    start_profil = final_text.lower().find(profil_marker)

    # Extraire poste
    if start_poste != -1:
        end_poste = start_profil if start_profil != -1 else len(final_text)
        description_poste = final_text[start_poste + len(poste_marker):end_poste].strip()
    else:
        description_poste = ""

    # Extraire profil
    if start_profil != -1:
        description_profil = final_text[start_profil + len(profil_marker):].strip()
    else:
        description_profil = ""

    return description_poste, description_profil

# Parcourir toutes les offres et ajouter les champs nettoyés
for job in data["offers"]:
    poste, profil = extract_sections(job["description_raw"])
    job["description_poste"] = poste
    job["description_profil"] = profil

# Sauvegarder dans un nouveau fichier JSON
with open("job_offers_wttj_clean_v3.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Nettoyage avec poste et profil terminé, JSON sauvegardé dans job_offers_wttj_clean_v3.json")
